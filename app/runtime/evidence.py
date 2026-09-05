# -*- coding: utf-8 -*-
"""终态证据收存（蓝图 §6.9/§11.1 单机子集）：工作区产物快照→CAS→
AttemptTerminalEnvelope 签发→pi_artifacts/pi_terminal_envelopes 归档。

快照边界（如实）：文件 ≤ max_artifact_files 且单文件 ≤ max_artifact_bytes
才入 CAS；超限在信封 runtimeObserved.missingEvidenceReasons 披露
（artifacts-truncated: ...），不因此失败任务；信封归档前必过
verified_terminal_envelope 语义校验，非法即抛 CasError（信封不应出现）。
"""
from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

from app.config import settings
from app.runtime.cas import put_bytes
from app.runtime.terminal import (
    build_terminal_envelope,
    verified_terminal_envelope,
    verify_terminal_signature,
)


def snapshot_workspace(workspace_dir: Path) -> tuple[list[dict], list[str]]:
    """工作区产物快照（相对路径/内容 digest/大小/类型）。

    评审 block-3：lstat 跳过 symlink/非普通文件（FIFO/设备不读，防外部文件
    收入与阻塞）；O_NOFOLLOW 打开 + fstat 复核 S_ISREG（防检查与读取间
    symlink 竞态）。返回 (artifacts, missing)。
    """
    artifacts: list[dict] = []
    missing: list[str] = []
    files = []
    for dirpath, dirnames, filenames in os.walk(workspace_dir):
        # 评审 block：目录 symlink 显式识别并从遍历中剔除（不静默漏报）
        kept = []
        for d in sorted(dirnames):
            dpath = Path(dirpath) / d
            try:
                st = dpath.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                missing.append(
                    f"artifact-skipped-dir-symlink: "
                    f"{dpath.relative_to(workspace_dir).as_posix()}")
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(workspace_dir).as_posix()
            try:
                st = full.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                missing.append(f"artifact-skipped-not-regular: {rel}")
                continue
            files.append((rel, full, st.st_size))
    files.sort(key=lambda t: t[0])
    if len(files) > settings.max_artifact_files:
        missing.append(
            f"artifacts-truncated: {len(files)} files > {settings.max_artifact_files}")
    for rel, full, size in files[:settings.max_artifact_files]:
        data, actual_size = _read_regular(full, settings.max_artifact_bytes)
        if data is None:
            # 竞态中被替换为 symlink/非普通文件，或实际大小超上限
            missing.append(f"artifact-skipped: {rel}（非普通文件或超上限）")
            continue
        digest = put_bytes(data)
        artifacts.append({"path": rel, "digest": digest, "size": actual_size,
                          "kind": "file"})
    return artifacts, missing


def _read_regular(path: Path, limit: int) -> tuple[bytes | None, int]:
    """O_NOFOLLOW 打开 + fstat 复核普通文件 + 有界读取（评审 block-3：
    以实际读取长度归档，杜绝文件增长绕过 max_artifact_bytes/大内存读取；
    finally 关闭 fd 防描述符泄漏）。返回 (data, actual_size)；异常或超限
    返回 (None, 0)。"""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None, 0
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, 0
        if st.st_size > limit:
            return None, 0
        data, total = b"", 0
        while True:
            chunk = os.read(fd, limit + 1 - total)  # 循环读防 short-read（评审 block）
            if not chunk:
                break
            data += chunk
            total += len(chunk)
            if total > limit:  # 读取与 fstat 竞态增长：视为超限拒绝
                return None, 0
        return data, total
    except OSError:
        return None, 0
    finally:
        os.close(fd)


def ingest_step_evidence(conn, *, task_id: str, attempt_id: str, run_id: str,
                         step_index: int, workspace_dir: Path,
                         outcome_class: str, status: str,
                         stop_reason: str | None, error: str | None = None) -> dict:
    """收存一步终态证据（同事务：CAS put 独立 commit、信封与表写入由调用方
    事务提交或回滚）；返回信封快照。"""
    artifacts, missing = snapshot_workspace(workspace_dir)
    if outcome_class == "SUCCESS_COMPLETE" and missing:
        # 评审 should-fix-2：截断/超限=证据不完整，阻止完整成功（不静默降级）
        raise ValueError(
            f"SUCCESS_COMPLETE 收存遇到缺失证据: {missing[:3]}（放宽 "
            f"PI_MAX_ARTIFACT_FILES/PI_MAX_ARTIFACT_BYTES 或缩小产物）")
    env = build_terminal_envelope(
        task_id=task_id, attempt_id=attempt_id, run_id=run_id,
        step_index=step_index, outcome_class=outcome_class, status=status,
        stop_reason=stop_reason or error, result_artifacts=artifacts,
        missing_evidence=missing)
    problems = verified_terminal_envelope(env)
    if problems:
        raise ValueError("信封未通过契约语义校验: " + "; ".join(problems[:3]))
    if not verify_terminal_signature(env):  # 评审 block-1：归档前必须真实验签
        raise ValueError("信封 Ed25519 验签失败（issuer/keyId/value 不符）")

    with conn.cursor() as cur:
        for a in artifacts:
            cur.execute(
                "INSERT INTO pi_artifacts (artifact_id, task_id, run_id, step_index, "
                "path, digest, size, kind) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (uuid.uuid4().hex[:16], task_id, run_id, step_index, a["path"],
                 a["digest"], a["size"], a["kind"]))
        cur.execute(
            "INSERT INTO pi_terminal_envelopes (envelope_id, task_id, attempt_id, "
            "run_id, step_index, outcome_class, status, envelope, verified_ok) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb, TRUE)",
            (env["terminalEnvelopeId"], task_id, attempt_id, run_id, step_index,
             outcome_class, status,
             json.dumps(env, ensure_ascii=False)))
    return env


def verify_envelope_integrity(conn, envelope_id: str) -> bool:
    """复核归档信封：真实验签 + CAS 每个产物 digest 可重读校验 + 语义合法
    （评审 block-1/2：完整性复核必须含验签，DB 中篡改 signature 亦检出）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT envelope FROM pi_terminal_envelopes WHERE envelope_id=%s",
                    (envelope_id,))
        row = cur.fetchone()
        if row is None:
            return False
        env = row["envelope"]
        from app.runtime.cas import verify_digest
        if not verify_terminal_signature(env):
            return False
        for a in env["resultArtifacts"]:
            if not verify_digest(a["digest"]):
                return False
        return verified_terminal_envelope(env) == []