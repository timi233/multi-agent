# -*- coding: utf-8 -*-
"""CommitBundle 契约语义校验与 Git 交付（蓝图 §10.10 单机子集）。

verified_commit_bundle(bundle) -> list[str]：先 Schema（形状）再语义：
- commitBundleId 必须等于由完整不可变前像重算的派生值（评审 block-2：
  ID 不再信任对象自报，parent/opKey/metadata 等任一变化必变 ID）；
- pathPolicyDigest 必须等于固定 trivial 政策常量（评审 block-1）；
- parentGitObjectIds 按 hex 键全局唯一（schema const sha1 + uniqueItems
  下语义等价不可达，保留为防御）；
- proposedCommitGitObjectId 与 treeGitObjectId 必须均为 sha1；
- self-digest 重算兜底。
digest 重算异常收敛为问题列表。运行时强校验（预计算 commit 对象 ===
实际读回、metadata 可重算）由本地 gitstager 交付路径完成。
"""
from __future__ import annotations

import hashlib

from app.contracts.codec import (
    ContractError,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
    validate,
)

EMPTY_TREE_SHA256 = ("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495"
                     "991b7852b855")
PATH_POLICY_DIGEST = "sha256:" + hashlib.sha256(jcs({"policy": "trivial"})).hexdigest()


def commit_bundle_id(*, commit_intent_id: str, commit_intent_digest: str,
                     attempt_id: str, source_bundle_digest: str,
                     manifest_digest: str, tree_sha256: str,
                     tree_git_id: dict, parents: list[dict],
                     metadata_digest: str, op_key: str,
                     commit_git_id: dict, path_policy_digest: str) -> str:
    """共享 ID 派生（gen 脚本与运行时同源；评审 block-2：
    除 ID/self-digest/signature 外的完整不可变前像）。"""
    blob = jcs({
        "commitIntentId": commit_intent_id,
        "commitIntentDigest": commit_intent_digest,
        "selectedAttemptId": attempt_id,
        "sourceBundleDigest": source_bundle_digest,
        "outputArtifactManifestDigest": manifest_digest,
        "proposedTreeDigest": tree_sha256,
        "treeGitObjectId": tree_git_id,
        "parentGitObjectIds": sorted(parents, key=lambda p: p["hex"]),
        "normalizedCommitMetadataDigest": metadata_digest,
        "operationIdempotencyKey": op_key,
        "proposedCommitGitObjectId": commit_git_id,
        "pathPolicyDigest": path_policy_digest,
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def _semantic_checks(bundle: dict) -> list[str]:
    problems: list[str] = []
    try:
        recomputed_id = commit_bundle_id(
            commit_intent_id=bundle["commitIntentId"],
            commit_intent_digest=bundle["commitIntentDigest"],
            attempt_id=bundle["selectedAttemptId"],
            source_bundle_digest=bundle["sourceBundleDigest"],
            manifest_digest=bundle["outputArtifactManifestDigest"],
            tree_sha256=bundle["proposedTreeDigest"],
            tree_git_id=bundle["treeGitObjectId"],
            parents=bundle.get("parentGitObjectIds") or [],
            metadata_digest=bundle["normalizedCommitMetadataDigest"],
            op_key=bundle["operationIdempotencyKey"],
            commit_git_id=bundle["proposedCommitGitObjectId"],
            path_policy_digest=bundle["pathPolicyDigest"])
    except Exception as exc:
        problems.append(f"commitBundleId 无法重算: {exc}")
        recomputed_id = None
    if recomputed_id is not None and bundle.get("commitBundleId") != recomputed_id:
        problems.append(
            f"commitBundleId 与不可变前像重算不一致: object={bundle.get('commitBundleId')} "
            f"recomputed={recomputed_id}")
    if bundle.get("pathPolicyDigest") != PATH_POLICY_DIGEST:
        problems.append("pathPolicyDigest 必须等于固定 trivial 政策常量（评审 block-1）")
    parents = bundle.get("parentGitObjectIds") or []
    hexes = [p.get("hex") for p in parents]
    if len(hexes) != len(set(hexes)):
        problems.append("parentGitObjectIds 存在重复 hex（同父引用两次）")
    tree_algo = (bundle.get("treeGitObjectId") or {}).get("algorithm")
    commit_algo = (bundle.get("proposedCommitGitObjectId") or {}).get("algorithm")
    if tree_algo != "sha1" or commit_algo != "sha1":
        problems.append("treeGitObjectId/proposedCommitGitObjectId 算法必须均为 sha1")
    try:
        recomputed = payload_digest(bundle, load_digest_profile("commit_bundle", "2"))
    except ContractError as exc:
        problems.append(f"payloadDigest 无法重算: {exc}")
        recomputed = None
    if recomputed is not None and bundle.get("payloadDigest") != recomputed:
        problems.append(
            f"payloadDigest self 不一致: object={bundle.get('payloadDigest')} "
            f"recomputed={recomputed}")
    return problems


def verified_commit_bundle(bundle: dict) -> list[str]:
    schema_problems = validate(bundle, load_schema("commit_bundle", "2"))
    if schema_problems:
        return schema_problems
    return _semantic_checks(bundle)


def verify_commit_bundle_signature(bundle: dict) -> bool:
    """验签：元数据绑定 assembler 身份 + 三方 digest 一致 + Ed25519 验签。"""
    from app.contracts.codec import SIGNATURE_ENVELOPE_KEYS, build_signature_envelope
    from app.security import keys as node_keys
    try:
        sig = bundle["signature"]
        if sig.get("objectType") != "commit_bundle" or sig.get("schemaVersion") != "2":
            return False
        if sig.get("keyId") != node_keys.key_id():
            return False
        if sig.get("issuer") != "pi.commit-assembler" or \
                sig.get("issuerWorkloadIdentity") != "pi.commit-assembler":
            return False
        if sig.get("audience") != "pi.platform":
            return False
        recomputed = payload_digest(bundle, load_digest_profile("commit_bundle", "2"))
        if sig.get("payloadDigest") != recomputed or \
                bundle.get("payloadDigest") != recomputed:
            return False
        meta = {k: sig[k] for k in SIGNATURE_ENVELOPE_KEYS if k in sig}
        _env, sig_in, _ = build_signature_envelope(
            bundle, load_schema("commit_bundle", "2"),
            load_digest_profile("commit_bundle", "2"), meta)
        return node_keys.verify(sig_in, sig["value"])
    except Exception:
        return False


def git_staging_result_id(*, commit_bundle_id_: str, commit_bundle_digest: str,
                          repository_id: str, candidate_ref: str,
                          expected: dict | None, applied: dict,
                          git_staging_epoch: int, op_key: str) -> str:
    """GitStagingResult 共享 ID 派生（gen 脚本与运行时同源）：
    除 ID/self-digest/signature 外的完整不可变前像；stagedAt 是运行事实
    不进 ID（幂等语义：同数据重试 ID 稳定，结果可复用）。"""
    blob = jcs({
        "commitBundleId": commit_bundle_id_,
        "commitBundleDigest": commit_bundle_digest,
        "repositoryId": repository_id,
        "candidateRef": candidate_ref,
        "expectedRefGitObjectId": expected,
        "appliedCommitGitObjectId": applied,
        "controlPlaneEpoch": 0,
        "gitStagingEpoch": git_staging_epoch,
        "revocationEpoch": 0,
        "operationIdempotencyKey": op_key,
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def _staging_result_semantic_checks(result: dict) -> list[str]:
    problems: list[str] = []
    try:
        recomputed_id = git_staging_result_id(
            commit_bundle_id_=result["commitBundleId"],
            commit_bundle_digest=result["commitBundleDigest"],
            repository_id=result["repositoryId"],
            candidate_ref=result["candidateRef"],
            expected=result["expectedRefGitObjectId"],
            applied=result["appliedCommitGitObjectId"],
            git_staging_epoch=result["gitStagingEpoch"],
            op_key=result["operationIdempotencyKey"])
    except Exception as exc:
        problems.append(f"gitStagingResultId 无法重算: {exc}")
        recomputed_id = None
    if recomputed_id is not None and \
            result.get("gitStagingResultId") != recomputed_id:
        problems.append(
            f"gitStagingResultId 与不可变前像重算不一致: "
            f"object={result.get('gitStagingResultId')} recomputed={recomputed_id}")
    for field in ("expectedRefGitObjectId", "appliedCommitGitObjectId"):
        obj = result.get(field)
        if obj is None:
            continue
        if obj.get("algorithm") != "sha1":
            problems.append(f"{field} 算法必须为 sha1")
    try:
        recomputed = payload_digest(result, load_digest_profile("git_staging_result", "2"))
    except ContractError as exc:
        problems.append(f"payloadDigest 无法重算: {exc}")
        recomputed = None
    if recomputed is not None and result.get("payloadDigest") != recomputed:
        problems.append(
            f"payloadDigest self 不一致: object={result.get('payloadDigest')} "
            f"recomputed={recomputed}")
    return problems


def verified_git_staging_result(result: dict) -> list[str]:
    """先 Schema（形状）再语义（ID 重算/对象算法/self-digest）。"""
    schema_problems = validate(result, load_schema("git_staging_result", "2"))
    if schema_problems:
        return schema_problems
    return _staging_result_semantic_checks(result)


def verify_git_staging_result_signature(result: dict) -> bool:
    """验签：元数据绑定 git-stager 身份 + 三方 digest 一致 + Ed25519 验签。"""
    from app.contracts.codec import SIGNATURE_ENVELOPE_KEYS, build_signature_envelope
    from app.security import keys as node_keys
    try:
        sig = result["signature"]
        if sig.get("objectType") != "git_staging_result" or \
                sig.get("schemaVersion") != "2":
            return False
        if sig.get("keyId") != node_keys.key_id():
            return False
        if sig.get("issuer") != "pi.git-stager" or \
                sig.get("issuerWorkloadIdentity") != "pi.git-stager":
            return False
        if sig.get("audience") != "pi.platform":
            return False
        recomputed = payload_digest(
            result, load_digest_profile("git_staging_result", "2"))
        if sig.get("payloadDigest") != recomputed or \
                result.get("payloadDigest") != recomputed:
            return False
        meta = {k: sig[k] for k in SIGNATURE_ENVELOPE_KEYS if k in sig}
        _env, sig_in, _ = build_signature_envelope(
            result, load_schema("git_staging_result", "2"),
            load_digest_profile("git_staging_result", "2"), meta)
        return node_keys.verify(sig_in, sig["value"])
    except Exception:
        return False


# ---------------------------------------------------------------------------
# G5-3：本地 delivery repo 交付（蓝图 §10.10 单机子集）。
# 每 task 独立 git 仓库（deliveries/<task_id>/，gitignore 排除），确定性提交：
#   tree   = 产物全部重建后 write-tree；tree sha1 + tree 对象 sha256；
#   commit = 固定 author/committer/时间戳 0 + message（含 opKey trailer），
#            由 tree/parent 字节构造 commit 对象并预计算 sha1；
#   强校验 = git hash-object -w 实际写出的对象 sha 必须等于预计算，且
#            update-ref 后 git rev-parse 读回必须等于预计算（预计算===实际）。
# 幂等：同 (task, operationIdempotencyKey) 复用既有 GitStagingResult，
# 不重复写 ref（同键重试必须给出同一证明）。
# ---------------------------------------------------------------------------
import datetime as _dt
import fcntl as _fcntl
import json as _json
import shutil as _shutil
import subprocess as _subprocess
from pathlib import Path as _Path

from app.config import settings as _settings
from app.db import connect as _connect


class _RepoLock:
    """仓库级跨进程互斥（评审 block-3）：`.git/pi-staging.lock` flock 排他，
    保证同一 delivery repo 的 write-tree/update-ref/归档串行推进。"""

    def __init__(self, repo: _Path) -> None:
        self._file = open(repo / ".git" / "pi-staging.lock", "a+")

    def __enter__(self) -> "_RepoLock":
        _fcntl.flock(self._file.fileno(), _fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        _fcntl.flock(self._file.fileno(), _fcntl.LOCK_UN)
        self._file.close()


class GitStagingError(Exception):
    """本地 git 交付失败（产物缺失/git 校验失败/对象不一致）。"""


def _git(repo: _Path, *args: str, input_: bytes | None = None) -> str:
    proc = _subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=120, input=(
            input_.decode("utf-8") if input_ is not None else None))
    if proc.returncode != 0:
        raise GitStagingError(
            f"git {' '.join(args)} 失败 rc={proc.returncode}: "
            f"{proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def _git_bytes(repo: _Path, *args: str) -> bytes:
    proc = _subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise GitStagingError(
            f"git {' '.join(args)} 失败 rc={proc.returncode}: "
            f"{proc.stderr.decode(errors='replace').strip()[:300]}")
    return proc.stdout


def _sha1(data: bytes) -> str:
    return hashlib.new("sha1", data, usedforsecurity=False).hexdigest()


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _task_artifacts(task_id: str) -> list[dict]:
    """读取待交付产物快照（kind=file，path 稳定排序）。
    pi_artifacts 无 attempt 维度列（单机单 attempt 语义，多 attempt/多 run
    未实现 DFS）→ 全量产物即终态快照；重复 path 视为非法（同一路径多版本
    必须人工消解，防确定性提交被历史产物污染）。"""
    rows = list(_connect().execute(
        "SELECT path, digest, size FROM pi_artifacts "
        "WHERE task_id = %s AND kind = 'file' ORDER BY path",
        (task_id,)).fetchall())
    paths = [r["path"] for r in rows]
    if len(paths) != len(set(paths)):
        raise GitStagingError(
            "产物存在重复 path（多版本未消解），拒绝交付；请人工消解产物")
    return rows


def _repo_path(task_id: str) -> _Path:
    repo = _settings.deliveries_dir / task_id
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Pi Platform")
        _git(repo, "config", "user.email", "pi@localhost")
        _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    return repo


def _current_head(repo: _Path) -> str | None:
    try:
        return _git(repo, "rev-parse", "--verify", "refs/heads/main")
    except GitStagingError:
        return None


def _commit_count(repo: _Path, head: str | None) -> int:
    if head is None:
        return 0
    out = _git(repo, "rev-list", "--count", "refs/heads/main")
    return int(out)


def _rebuild_worktree(repo: _Path, artifacts: list[dict]) -> None:
    """清空工作树（保留 .git）后重建产物文件，并 `git add -A` 更新 index
    （评审 block-1：write-tree 必须基于产物 index，禁止空 tree/陈旧 index）。

    路径防线（评审 block-5）：相对非绝对、无 .. 组分、且**任一组分不得为
    .git**（防覆盖仓库元数据 config/HEAD/refs 等）。"""
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            _shutil.rmtree(child)
        else:
            child.unlink()
    for row in artifacts:
        rel = _Path(row["path"])
        if rel.is_absolute() or ".." in rel.parts or \
                any(part == ".git" for part in rel.parts):
            raise GitStagingError(f"产物路径非法: {row['path']}")
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        from app.runtime.cas import get as cas_get
        data = cas_get(row["digest"])
        # 评审 block-9：写入前重算 SHA-256 并核对 size——CAS 损坏/篡改时
        # manifest（DB 自报 digest）与 tree 实际内容必须一致，否则拒绝，
        # 防止 CommitBundle/GitStagingResult 双签名对失配内容背书。
        if _sha256(data) != row["digest"] or len(data) != row["size"]:
            raise GitStagingError(
                f"产物校验失败: path={row['path']} digest/size 与 CAS 实际"
                f"内容不一致（digest={row['digest']} size={row['size']} "
                f"actual_size={len(data)}），拒绝交付")
        target.write_bytes(data)
    # 显式更新 index（清空后全量重建；write-tree 读 index）。
    # 评审 block-6：`-f` 强制——产物自身可能包含 .gitignore 规则，
    # 默认 add -A 会静默排除合法 artifact，破坏 manifest/tree 精确绑定。
    _git(repo, "add", "-f", "-A")


def _tree_of_commit(repo: _Path, commit_hex: str) -> bytes:
    tree_id = _git(repo, "rev-parse", f"{commit_hex}^{{tree}}")
    return _git_bytes(repo, "cat-file", "tree", tree_id)


def _commit_object_bytes(tree_hex: str, parents: list[str], message: str) -> bytes:
    lines = [f"tree {tree_hex}"]
    lines += [f"parent {p}" for p in parents]
    identity = "Pi Platform <pi@localhost> 0 +0000"
    return ("\n".join(lines) + f"\nauthor {identity}\ncommitter {identity}\n\n"
            + message).encode("utf-8")


def _sign_with(obj: dict, schema, profile, sig_name: str, issuer: str) -> dict:
    """按验证方同等元数据规则签发（identity/audience 与 verify_* 对称）。"""
    from app.contracts.codec import SIGNATURE_ENVELOPE_KEYS, build_signature_envelope
    from app.security import keys as node_keys
    meta = {
        "objectType": sig_name, "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": node_keys.key_id(),
        "issuer": issuer, "issuerWorkloadIdentity": issuer,
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": _dt.datetime.now(_dt.timezone.utc).isoformat()
        .replace("+00:00", "Z"),
    }
    _env, sig_in, _ = build_signature_envelope(obj, schema, profile, meta)
    return {**_env, "value": node_keys.sign(sig_in)}


def _assemble_bundle(task_id: str, artifacts: list[dict], *,
                     repo: _Path, ref_head: str | None, epoch: int,
                     op_key: str, attempt_id: str,
                     tree_hex: str, tree_bytes: bytes
                     ) -> tuple[dict, str, bytes, str]:
    """确定性组装 CommitBundle（assembler 签名）+ commit 对象/预计算 sha1。
    ref_head=staging 前 ref 状态（决定 parent/sourceBundleDigest/expected）。"""
    manifest = _json.dumps(sorted([{
        "path": r["path"], "digest": r["digest"], "size": r["size"]}
        for r in artifacts], key=lambda x: x["path"]),
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_digest = _sha256(manifest)

    parents = [ref_head] if ref_head else []
    op_trailer = (f"pi: task {task_id} staging\n\n"
                  f"X-Platform-Operation-Key: {op_key}\n")
    metadata = {
        "author": {"name": "Pi Platform", "email": "pi@localhost"},
        "committer": {"name": "Pi Platform", "email": "pi@localhost"},
        "message": op_trailer, "timestampEpoch": 0,
    }
    metadata_digest = _sha256(jcs(metadata))

    commit_raw = _commit_object_bytes(tree_hex, parents, op_trailer)
    commit_hex = _sha1(b"commit " + str(len(commit_raw)).encode() + b"\x00"
                       + commit_raw)
    source_digest = (EMPTY_TREE_SHA256 if not ref_head
                     else _sha256(_tree_of_commit(repo, ref_head)))

    bundle_payload = {
        "contractVersion": "2", "workloadIdentity": "pi.commit-assembler",
        "commitIntentId": hashlib.sha256(task_id.encode()).hexdigest()[:16],
        "commitIntentDigest": _sha256(jcs({"taskId": task_id})),
        "selectedAttemptId": attempt_id,
        "sourceBundleDigest": source_digest,
        "outputArtifactManifestDigest": manifest_digest,
        "proposedTreeDigest": _sha256(tree_bytes),
        "treeGitObjectId": {"algorithm": "sha1", "hex": tree_hex},
        "parentGitObjectIds": [{"algorithm": "sha1", "hex": p} for p in parents],
        "normalizedCommitMetadataDigest": metadata_digest,
        "operationIdempotencyKey": op_key,
        "proposedCommitGitObjectId": {"algorithm": "sha1", "hex": commit_hex},
        "pathPolicyDigest": PATH_POLICY_DIGEST,
    }
    bundle_payload["commitBundleId"] = commit_bundle_id(
        commit_intent_id=bundle_payload["commitIntentId"],
        commit_intent_digest=bundle_payload["commitIntentDigest"],
        attempt_id=bundle_payload["selectedAttemptId"],
        source_bundle_digest=bundle_payload["sourceBundleDigest"],
        manifest_digest=bundle_payload["outputArtifactManifestDigest"],
        tree_sha256=bundle_payload["proposedTreeDigest"],
        tree_git_id=bundle_payload["treeGitObjectId"],
        parents=bundle_payload["parentGitObjectIds"],
        metadata_digest=bundle_payload["normalizedCommitMetadataDigest"],
        op_key=bundle_payload["operationIdempotencyKey"],
        commit_git_id=bundle_payload["proposedCommitGitObjectId"],
        path_policy_digest=bundle_payload["pathPolicyDigest"])
    bundle_payload["signature"] = {
        "objectType": "commit_bundle", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "placeholder",
        "issuer": "placeholder",
        "issuerWorkloadIdentity": "pi.commit-assembler",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": _dt.datetime.now(_dt.timezone.utc).isoformat()
        .replace("+00:00", "Z"),
        "payloadDigest": "sha256:" + "0" * 64,
        "value": "A" * 86 + "==",
    }
    bundle_payload["payloadDigest"] = payload_digest(
        bundle_payload, load_digest_profile("commit_bundle", "2"))
    bundle_payload["signature"] = _sign_with(
        bundle_payload, load_schema("commit_bundle", "2"),
        load_digest_profile("commit_bundle", "2"), "commit_bundle",
        "pi.commit-assembler")
    return bundle_payload, commit_hex, commit_raw, op_trailer


def _make_result(bundle_payload: dict, task_id: str, *, ref_head: str | None,
                 epoch: int, op_key: str, commit_hex: str) -> dict:
    """组装并自校验 GitStagingResult（git-stager 签名）。ref_head=expected。"""
    result = {
        "contractVersion": "2", "workloadIdentity": "pi.git-stager",
        "commitBundleId": bundle_payload["commitBundleId"],
        "commitBundleDigest": bundle_payload["payloadDigest"],
        "repositoryId": hashlib.sha256(task_id.encode()).hexdigest()[:16],
        "candidateRef": "refs/heads/main",
        "expectedRefGitObjectId": ({"algorithm": "sha1", "hex": ref_head}
                                   if ref_head else None),
        "appliedCommitGitObjectId": {"algorithm": "sha1", "hex": commit_hex},
        "controlPlaneEpoch": 0, "gitStagingEpoch": epoch, "revocationEpoch": 0,
        "operationIdempotencyKey": op_key,
        "stagedAt": _dt.datetime.now(_dt.timezone.utc).isoformat()
        .replace("+00:00", "Z"),
    }
    result["gitStagingResultId"] = git_staging_result_id(
        commit_bundle_id_=result["commitBundleId"],
        commit_bundle_digest=result["commitBundleDigest"],
        repository_id=result["repositoryId"], candidate_ref=result["candidateRef"],
        expected=result["expectedRefGitObjectId"],
        applied=result["appliedCommitGitObjectId"],
        git_staging_epoch=result["gitStagingEpoch"],
        op_key=result["operationIdempotencyKey"])
    result["signature"] = {
        "objectType": "git_staging_result", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "placeholder",
        "issuer": "placeholder",
        "issuerWorkloadIdentity": "pi.git-stager",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": _dt.datetime.now(_dt.timezone.utc).isoformat()
        .replace("+00:00", "Z"),
        "payloadDigest": "sha256:" + "0" * 64,
        "value": "A" * 86 + "==",
    }
    result["payloadDigest"] = payload_digest(
        result, load_digest_profile("git_staging_result", "2"))
    result["signature"] = _sign_with(
        result, load_schema("git_staging_result", "2"),
        load_digest_profile("git_staging_result", "2"), "git_staging_result",
        "pi.git-stager")
    problems = verified_git_staging_result(result)
    if problems:
        raise GitStagingError(
            f"GitStagingResult 语义校验失败: {'; '.join(problems)}")
    if not verify_git_staging_result_signature(result):
        raise GitStagingError("GitStagingResult 验签失败（签发自身闭环不通过）")
    return result


def _archived_result(task_id: str, op_key: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT result FROM pi_git_staging_results "
            "WHERE task_id = %s AND operation_idempotency_key = %s",
            (task_id, op_key)).fetchone()
        return row["result"] if row is not None else None


def _archive_result(conn, result: dict, task_id: str) -> None:
    try:
        conn.execute(
            "INSERT INTO pi_git_staging_results "
            "(result_id, task_id, commit_bundle_id, commit_bundle_digest, "
            " operation_idempotency_key, repository_id, candidate_ref, "
            " applied_commit_id, git_staging_epoch, result, verified_ok) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, TRUE)",
            (result["gitStagingResultId"], task_id,
             result["commitBundleId"], result["commitBundleDigest"],
             result["operationIdempotencyKey"], result["repositoryId"],
             result["candidateRef"], result["appliedCommitGitObjectId"]["hex"],
             result["gitStagingEpoch"],
             _json.dumps(result, ensure_ascii=False)))
    except Exception as exc:  # UNIQUE(task, op_key) 并发兜底 → 复用既有
        row = conn.execute(
            "SELECT result FROM pi_git_staging_results "
            "WHERE task_id = %s AND operation_idempotency_key = %s",
            (task_id, op_key)).fetchone()
        if row is None:
            raise GitStagingError(f"结果归档失败: {exc}") from exc


def _head_opkey(repo: _Path, head: str, op_key: str) -> bool:
    """head 提交是否是本平台同 opKey 确定性提交（评审 block-7 恢复识别）。"""
    try:
        msg = _git(repo, "log", "-1", "--format=%B", head)
    except GitStagingError:
        return False
    return f"X-Platform-Operation-Key: {op_key}" in msg


def _try_recover_from_head(repo: _Path, task_id: str, artifacts: list[dict],
                           head: str | None, op_key: str,
                           attempt_id: str) -> dict | None:
    """评审 block-7/8 崩溃恢复：update-ref 已推进但 INSERT 未完成时（进程崩溃/
    DB 失败），head 已是同 opKey 确定性提交 → 从**当前产物全量重建工作树与
    index**，write-tree 的 tree 必须与 HEAD tree 逐字节同源（内容寻址，证明
    当前产物状态可重建该提交），再重算确定性前像（=== head 强校验）重建
    GitStagingResult 归档并复用，**不再推进 ref、不产生第二个 commit**。
    产物被改动/无法重建 HEAD tree 时拒绝恢复。返回可复用 result；head 无关
    返回 None 走正常路径。"""
    if head is None or not _head_opkey(repo, head, op_key):
        return None
    parent = None
    try:
        parent = _git(repo, "rev-parse", f"{head}^")
    except GitStagingError:
        pass
    # 产物状态必须能够重建 HEAD tree（评审 block-8：恢复证据绑定当前 manifest）
    _rebuild_worktree(repo, artifacts)
    rebuilt_tree = _git(repo, "write-tree")
    head_tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
    if rebuilt_tree != head_tree:
        raise GitStagingError(
            f"恢复校验失败: 当前产物重建 tree={rebuilt_tree} 与 head "
            f"{head} tree={head_tree} 不一致（产物已变化），拒绝复用")
    tree_bytes = _git_bytes(repo, "cat-file", "tree", head_tree)
    epoch = _commit_count(repo, head)
    bundle_payload, commit_hex, _raw, _op = _assemble_bundle(
        task_id, artifacts, repo=repo, ref_head=parent, epoch=epoch,
        op_key=op_key, attempt_id=attempt_id, tree_hex=head_tree,
        tree_bytes=tree_bytes)
    if commit_hex != head:
        # head 与本平台确定性前像不符 → 绝不能当作本 opKey 已应用提交
        raise GitStagingError(
            f"head {head} 的 opKey 匹配但确定性前像不符（预计算 {commit_hex}），"
            "拒绝恢复；仓库状态异常")
    return _make_result(bundle_payload, task_id, ref_head=parent, epoch=epoch,
                        op_key=op_key, commit_hex=head)


def stage_commit(task_id: str, attempt_id: str | None = None,
                 op_key: str | None = None) -> dict:
    """执行确定性 git 交付：CommitBundle（assembler 视角签名）+ 读回证据
    GitStagingResult（git-stager 视角签名），DB 归档。返回 result 对象。
    opKey 幂等：同键复用既有证明；崩溃残留（ref 已推进、归档缺失）由
    _try_recover_from_head 从确定性前像识别并复用，不产生第二提交。"""
    from app.contracts.codec import (  # noqa: F401
        load_digest_profile, load_schema, payload_digest,
    )
    from app.runtime.cas import get as cas_get  # noqa: F401
    if not task_id or len(task_id) != 16 or \
            not all(c in "0123456789abcdef" for c in task_id):
        raise GitStagingError(f"task_id 非法: {task_id!r}（必须 16hex，防路径穿越）")
    op_key = op_key or hashlib.sha256(f"{task_id}:staging".encode()).hexdigest()[:32]
    attempt_id = attempt_id or hashlib.sha256(f"attempt:{task_id}".encode()).hexdigest()[:16]

    existing = _archived_result(task_id, op_key)
    if existing is not None:
        return existing  # 幂等：同键复用既有证明

    artifacts = _task_artifacts(task_id)
    if not artifacts:
        raise GitStagingError(f"task {task_id} 无产物（kind=file），拒绝无声名交付")
    repo = _repo_path(task_id)
    with _RepoLock(repo):  # 评审 block-3：仓库级跨进程互斥，禁止并发推进同一 ref
        existing = _archived_result(task_id, op_key)  # 锁内幂等复查
        if existing is not None:
            return existing
        head = _current_head(repo)
        recovered = _try_recover_from_head(repo, task_id, artifacts, head,
                                           op_key, attempt_id)
        if recovered is not None:
            with _connect() as conn:
                _archive_result(conn, recovered, task_id)
            return recovered  # 崩溃恢复：复用已应用的提交，不推进 ref

        epoch = _commit_count(repo, head) + 1
        _rebuild_worktree(repo, artifacts)
        tree_hex = _git(repo, "write-tree")
        tree_bytes = _git_bytes(repo, "cat-file", "tree", tree_hex)
        bundle_payload, commit_hex, commit_raw, _op = _assemble_bundle(
            task_id, artifacts, repo=repo, ref_head=head, epoch=epoch,
            op_key=op_key, attempt_id=attempt_id, tree_hex=tree_hex,
            tree_bytes=tree_bytes)
        problems = verified_commit_bundle(bundle_payload)
        if problems:
            raise GitStagingError(
                f"CommitBundle 语义校验失败: {'; '.join(problems)}")
        if not verify_commit_bundle_signature(bundle_payload):
            # 评审 block-4：写对象前必须完成 assembler 验签闭环（自签自验）
            raise GitStagingError("CommitBundle 验签失败（签发自身闭环不通过）")

        # 写对象并强校验（预计算 === 实际写出的对象；stdin 传对象主体，
        # git 自行包装 "<type> <len>\0" 头，与预计算使用同一 content bytes）
        proc = _subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "-t", "commit",
             "--stdin"],
            input=commit_raw, capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise GitStagingError(
                "git hash-object 失败: "
                + proc.stderr.decode(errors="replace")[:300])
        written = proc.stdout.strip().decode()
        if written != commit_hex:
            raise GitStagingError(
                f"commit 对象不一致: 预计算={commit_hex} 实际写入={written}")
        # CAS 推进：必须从锁内确定的前像推进（old=读到的 head 或全零）。
        _git(repo, "update-ref", "refs/heads/main", commit_hex,
             head or "0" * 40)
        readback = _current_head(repo)
        if readback != commit_hex:
            raise GitStagingError(
                f"ref 读回不一致: 预计算={commit_hex} 读回={readback}")

        result = _make_result(bundle_payload, task_id, ref_head=head,
                              epoch=epoch, op_key=op_key, commit_hex=commit_hex)
        with _connect() as conn:
            _archive_result(conn, result, task_id)
        return result