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