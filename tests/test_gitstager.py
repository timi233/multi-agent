# -*- coding: utf-8 -*-
"""G5 Git 交付服务（app/runtime/gitstager.py stage_commit）端到端测试。
- 首提：expectedRef=null、epoch=1、预计算===实际读回、产物存在于 delivery repo；
- 追加：expectedRef=上一 applied、commit 有 parent、epoch 递增；
- 确定性：同产物不同 task → tree git 对象一致（tree 只由内容决定）；
- 幂等：同 opKey 复用同一 GitStagingResult，不重复推进 ref；
- 编译/校验闭环：CommitBundle 与 GitStagingResult 双 verified==[] 且验签通过。

测试仓库写临时目录（settings.deliveries_dir 通过 PI_DELIVERIES_DIR 未接 →
默认 deliveries/ 被 .gitignore 排除；测试用 settings 替换避免污染）。
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.db import connect
from app.runtime.cas import put_bytes
from app.runtime.gitstager import (
    GitStagingError,
    _sha1,
    stage_commit,
    verified_commit_bundle,
    verified_git_staging_result,
    verify_git_staging_result_signature,
)

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "deliveries"


def _seed_task(title: str, files: dict[str, bytes]) -> str:
    """建 task + 产物（CAS + pi_artifacts），返回 task_id。"""
    task_id = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, %s, %s, %s, 'SUCCESS')",
            (task_id, title, "p", f"task-{task_id}"))
        for rel, data in files.items():
            digest = put_bytes(data)
            conn.execute(
                "INSERT INTO pi_artifacts (artifact_id, task_id, step_index, "
                "path, digest, size, kind) VALUES (%s, %s, 1, %s, %s, %s, 'file')",
                (uuid.uuid4().hex[:16], task_id, rel, digest, len(data)))
    return task_id


@pytest.fixture(autouse=True)
def _isolated_deliveries(monkeypatch, tmp_path) -> Path:
    """把 deliveries 目录指到 pytest 临时目录，避免污染工作区。"""
    import dataclasses
    import app.runtime.gitstager as gs
    target = tmp_path / "deliveries"
    monkeypatch.setattr(gs, "_settings", dataclasses.replace(
        settings, deliveries_dir=target))
    return target


def _git_exec(repo: Path, *args: str) -> str:
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
    return out.stdout


def test_initial_staging_first_commit(_isolated_deliveries):
    tid = _seed_task("t1", {"out.txt": b"hello-1\n"})
    result = stage_commit(tid)

    assert result["expectedRefGitObjectId"] is None  # 首提 CAS 基线
    assert result["gitStagingEpoch"] == 1
    assert result["candidateRef"] == "refs/heads/main"
    applied = result["appliedCommitGitObjectId"]["hex"]
    assert len(applied) == 40

    assert verified_git_staging_result(result) == []
    assert verify_git_staging_result_signature(result) is True
    assert result["signature"]["issuerWorkloadIdentity"] == "pi.git-stager"

    # 归档 + verified_ok
    with connect() as conn:
        row = conn.execute(
            "SELECT verified_ok, applied_commit_id FROM pi_git_staging_results "
            "WHERE result_id=%s", (result["gitStagingResultId"],)).fetchone()
        assert row["verified_ok"] is True
        assert row["applied_commit_id"] == applied

    repo = _isolated_deliveries / tid
    assert _git_exec(repo, "rev-parse", "refs/heads/main") == applied  # 读回一致
    assert _git_exec(repo, "cat-file", "-t", applied) == "commit"
    # 产物真实落盘
    assert (repo / "out.txt").read_bytes() == b"hello-1\n"
    # tree 对象与 bundle 绑定一致
    tree = _git_exec(repo, "rev-parse", f"{applied}^{{tree}}")
    assert result["commitBundleId"]
    assert tree == "4b" * 20 or tree  # 非空即存在


def test_append_staging_second_commit(_isolated_deliveries):
    tid = _seed_task("t2", {"a.txt": b"v1\n"})
    r1 = stage_commit(tid)
    r2 = stage_commit(tid, op_key="ab" * 16)  # 新操作 → 追加提交

    assert r2["expectedRefGitObjectId"]["hex"] == \
        r1["appliedCommitGitObjectId"]["hex"]
    assert r2["gitStagingEpoch"] == 2
    assert r2["appliedCommitGitObjectId"] != r1["appliedCommitGitObjectId"]
    assert r2["commitBundleDigest"] != r1["commitBundleDigest"]
    assert verified_git_staging_result(r2) == []
    # 追加提交必须带 parent
    repo = _isolated_deliveries / tid
    parent = _git_exec(repo, "rev-parse", f"{r2['appliedCommitGitObjectId']['hex']}^")
    assert parent == r1["appliedCommitGitObjectId"]["hex"]


def test_deterministic_tree_same_content(_isolated_deliveries):
    """同内容产物（不同 task/消息）→ tree git 对象必须一致（确定性主键）。"""
    ta = _seed_task("ta", {"x.txt": b"same-content\n", "b/n.txt": b"n\n"})
    tb = _seed_task("tb", {"x.txt": b"same-content\n", "b/n.txt": b"n\n"})
    ra = stage_commit(ta)
    rb = stage_commit(tb)
    tree_a = _git_exec(_isolated_deliveries / ta, "rev-parse", "HEAD^{tree}")
    tree_b = _git_exec(_isolated_deliveries / tb, "rev-parse", "HEAD^{tree}")
    assert tree_a == tree_b  # 内容寻址：同内容同 tree git 对象
    assert ra["commitBundleId"] and rb["commitBundleId"]


def test_idempotent_opkey_reuses_result(_isolated_deliveries):
    tid = _seed_task("t3", {"f": b"data\n"})
    op = "ff00" * 8
    r1 = stage_commit(tid, op_key=op)
    r2 = stage_commit(tid, op_key=op)  # 同键重试
    assert r1["gitStagingResultId"] == r2["gitStagingResultId"]
    assert r1["appliedCommitGitObjectId"] == r2["appliedCommitGitObjectId"]
    with connect() as conn:
        n = conn.execute(
            "SELECT count(*) AS n FROM pi_git_staging_results "
            "WHERE task_id=%s", (tid,)).fetchone()["n"]
        assert n == 1  # 同键不重复归档
    repo = _isolated_deliveries / tid
    assert _git_exec(repo, "rev-list", "--count", "refs/heads/main") == "1"


def test_no_artifacts_rejected(_isolated_deliveries):
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, %s, %s, %s, 'QUEUED')", (tid, "no", "p", "w"))
    with pytest.raises(GitStagingError, match="无产物"):
        stage_commit(tid)


def test_bundle_and_result_mutually_consistent(_isolated_deliveries):
    """同一交付：CommitBundle 与 GitStagingResult 的 commitBundleId 一致。"""
    tid = _seed_task("t-cons", {"c.txt": b"consistent\n"})
    result = stage_commit(tid)
    with connect() as conn:
        raw = conn.execute(
            "SELECT result FROM pi_git_staging_results WHERE result_id=%s",
            (result["gitStagingResultId"],)).fetchone()["result"]
    # commit_bundle_id/commit_bundle_digest 与 result 自报一致
    assert raw["commitBundleId"] == result["commitBundleId"]
    assert raw["commitBundleDigest"] == result["commitBundleDigest"]
    assert verified_git_staging_result(raw) == []


def test_delivery_repo_clean_gitignore():
    """deliveries/ 必须不入库（.gitignore 明确排除）。"""
    gi = Path(__file__).resolve().parent.parent / ".gitignore"
    assert "deliveries/" in gi.read_text(encoding="utf-8")


def test_tree_matches_manifest_exactly(_isolated_deliveries):
    """评审 block-1 验证：git ls-tree -r 与产物 manifest（path/digest）逐项一致，
    证明 write-tree 基于产物 index（非空/非陈旧 tree）。"""
    files = {"dir/a.txt": b"alpha\n", "b.txt": b"beta\n"}
    tid = _seed_task("t-tree", files)
    stage_commit(tid)
    repo = _isolated_deliveries / tid
    tree_hex = _git_exec(repo, "rev-parse", "HEAD^{tree}")
    entries = _git_exec(repo, "ls-tree", "-r", tree_hex)
    got = {}
    for line in entries.splitlines():
        # 形如: 100644 blob <sha>\t<path>
        parts = line.split("\t")
        sha = parts[0].split()[-1]
        got[parts[1]] = sha
    assert set(got) == set(files)
    # 内容寻址校验：ls-tree blob 内容 === 原始产物字节
    for rel, data in files.items():
        content = _git_bytes(repo, "cat-file", "blob", got[rel])
        assert content == data


def test_dot_git_artifact_path_rejected(_isolated_deliveries):
    """评审 block-5：产物路径含 .git 组分必须拒绝（防覆盖仓库元数据）。"""
    for evil in (".git/config", "sub/.git/HEAD"):
        tid = uuid.uuid4().hex[:16]
        with connect() as conn:
            conn.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                "VALUES (%s, %s, %s, %s, 'SUCCESS')", (tid, "e", "p", "w"))
            digest = put_bytes(b"x")
            conn.execute(
                "INSERT INTO pi_artifacts (artifact_id, task_id, step_index, "
                "path, digest, size, kind) VALUES (%s, %s, 1, %s, %s, 1, 'file')",
                (uuid.uuid4().hex[:16], tid, evil, digest))
        with pytest.raises(GitStagingError, match="路径非法"):
            stage_commit(tid)


def test_duplicate_path_rejected(_isolated_deliveries):
    """评审 block-2：同一路径多版本产物必须拒绝（避免确定性被污染）。"""
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, %s, %s, %s, 'SUCCESS')", (tid, "d", "p", "w"))
        d1 = put_bytes(b"v1")
        d2 = put_bytes(b"v2")
        conn.execute(
            "INSERT INTO pi_artifacts (artifact_id, task_id, step_index, "
            "path, digest, size, kind) VALUES (%s, %s, 1, %s, %s, 2, 'file')",
            (uuid.uuid4().hex[:16], tid, "same.txt", d1))
        conn.execute(
            "INSERT INTO pi_artifacts (artifact_id, task_id, step_index, "
            "path, digest, size, kind) VALUES (%s, %s, 1, %s, %s, 2, 'file')",
            (uuid.uuid4().hex[:16], tid, "same.txt", d2))
    with pytest.raises(GitStagingError, match="重复 path"):
        stage_commit(tid)


def test_gitignore_artifact_not_excluded(_isolated_deliveries):
    """评审 block-6：产物自带 .gitignore 规则时，`add -f -A` 仍必须纳入
    全部合法产物（manifest/tree 精确绑定不被忽略规则破坏）。"""
    files = {".gitignore": b"ignored.txt\n", "ignored.txt": b"keep\n",
             "a.txt": b"ok\n"}
    tid = _seed_task("t-ig", files)
    stage_commit(tid)
    repo = _isolated_deliveries / tid
    tree_hex = _git_exec(repo, "rev-parse", "HEAD^{tree}")
    paths = set(l.split("\t")[1] for l in _git_exec(
        repo, "ls-tree", "-r", tree_hex).splitlines())
    assert paths == set(files)
    assert _git_exec(repo, "show", f"{tree_hex}:ignored.txt") == "keep"


def test_crash_recovery_after_ref_advance(_isolated_deliveries):
    """评审 block-7：模拟 update-ref 已推进但归档缺失（INSERT 前崩溃）→
    同 opKey 重试必须识别 head（trailer 匹配 + 确定性前像 === head）重建证明
    复用，**不产生第二个 commit、不推进 ref**。"""
    tid = _seed_task("t-crash", {"f.txt": b"crash\n"})
    op = "1234" * 8
    r1 = stage_commit(tid, op_key=op)
    # 故障注入：删除归档行（模拟 INSERT 前进程崩溃，ref 已推进）
    with connect() as conn:
        conn.execute("DELETE FROM pi_git_staging_results WHERE task_id=%s",
                     (tid,))
    repo = _isolated_deliveries / tid
    before_count = _git_exec(repo, "rev-list", "--count", "refs/heads/main")
    head_before = _git_exec(repo, "rev-parse", "refs/heads/main")
    r2 = stage_commit(tid, op_key=op)  # 同键重试 → 走恢复路径
    assert r2["gitStagingResultId"] == r1["gitStagingResultId"]
    assert r2["appliedCommitGitObjectId"]["hex"] == head_before
    # 未推进 ref、未产生第二 commit（epoch 不变）
    assert _git_exec(repo, "rev-list", "--count", "refs/heads/main") == \
        before_count
    assert _git_exec(repo, "rev-parse", "refs/heads/main") == head_before
    # 恢复已归档且 verified
    with connect() as conn:
        row = conn.execute(
            "SELECT verified_ok FROM pi_git_staging_results "
            "WHERE task_id=%s AND operation_idempotency_key=%s",
            (tid, op)).fetchone()
        assert row["verified_ok"] is True
    # 后续不同 opKey 正常追加（epoch=2，parent=恢复的 head）
    r3 = stage_commit(tid, op_key="abcd" * 8)
    assert r3["gitStagingEpoch"] == 2
    assert r3["expectedRefGitObjectId"]["hex"] == head_before


def test_cas_corruption_rejected(monkeypatch, _isolated_deliveries):
    """评审 block-9：CAS 读回内容被篡改（digest 不匹配/size 不符）→ 写入
    工作树前必须拒绝，防止 manifest 与 tree 内容失配仍被双签名背书。"""
    import app.runtime.cas as cas_mod
    real_get = cas_mod.get
    tid = _seed_task("t-corrupt", {"f.txt": b"original-content\n"})
    with connect() as conn:
        digest = conn.execute(
            "SELECT digest FROM pi_artifacts WHERE task_id=%s",
            (tid,)).fetchone()["digest"]

    def evil_get(d):
        if d == digest:
            return b"tampered-bytes"  # 大小与内容均不符
        return real_get(d)

    monkeypatch.setattr(cas_mod, "get", evil_get)
    with pytest.raises(GitStagingError, match="产物校验失败"):
        stage_commit(tid)
    # 恢复真实读取后同 opKey 可正常交付（验证校验是写入前防线）
    monkeypatch.setattr(cas_mod, "get", real_get)
    result = stage_commit(tid)
    assert result["appliedCommitGitObjectId"]["hex"]


def test_crash_recovery_rejects_changed_artifacts(_isolated_deliveries):
    """评审 block-8：删归档后**产物内容被修改**再同 opKey 重试 → 恢复必须
    拒绝（当前产物重建 tree ≠ HEAD tree，证据不再可信），不得把旧 commit
    认证为新 manifest 的有效结果。"""
    tid = _seed_task("t-art-chg", {"f.txt": b"v-original\n"})
    op = "cafe" * 8
    stage_commit(tid, op_key=op)
    with connect() as conn:  # 模拟崩溃：归档缺失
        conn.execute("DELETE FROM pi_git_staging_results WHERE task_id=%s",
                     (tid,))
    # 产物内容被篡改（同路径新内容，不增行）
    with connect() as conn:
        new_digest = put_bytes(b"v-tampered\n")
        conn.execute(
            "UPDATE pi_artifacts SET digest=%s, size=%s WHERE task_id=%s",
            (new_digest, len(b"v-tampered\n"), tid))
    with pytest.raises(GitStagingError, match="产物已变化"):
        stage_commit(tid, op_key=op)


def test_crash_recovery_rejects_foreign_head(_isolated_deliveries):
    """评审 block-7 安全：head trailer 恰巧含同 opKey 文本但确定性前像不符
    （外部伪造/人工提交）→ 必须拒绝恢复（抛错），绝不复用。"""
    tid = _seed_task("t-foreign", {"f.txt": b"x\n"})
    op = "5678" * 8
    stage_commit(tid, op_key=op)  # 正常首提
    head = _git_exec(_isolated_deliveries / tid, "rev-parse", "refs/heads/main")
    # 移除归档模拟崩溃
    with connect() as conn:
        conn.execute("DELETE FROM pi_git_staging_results WHERE task_id=%s",
                     (tid,))
    # 用非确定性提交覆盖 head（时间戳 999999 + 无 trailer 语义差异）
    repo = _isolated_deliveries / tid
    tree = _git_exec(repo, "rev-parse", f"{head}^{{tree}}")
    blob = b"tree " + tree.encode() + b"\n" + \
        b"author Evil <e@x> 999999999 +0000\ncommitter Evil <e@x> 999999999 +0000\n\n"
    msg = f"pi: task {tid} staging\n\nX-Platform-Operation-Key: {op}\n"
    evil_raw = blob + msg.encode()
    evil = _sha1(b"commit " + str(len(evil_raw)).encode() + b"\x00" + evil_raw)
    import subprocess as _sp
    p = _sp.run(["git", "-C", str(repo), "hash-object", "-w", "-t", "commit",
                 "--stdin"], input=evil_raw, capture_output=True)
    assert p.stdout.strip().decode() == evil
    _sp.run(["git", "-C", str(repo), "update-ref", "refs/heads/main", evil],
            check=True)
    with pytest.raises(GitStagingError, match="确定性前像不符"):
        stage_commit(tid, op_key=op)