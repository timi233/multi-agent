# -*- coding: utf-8 -*-
"""生成 commit_bundle v2 契约向量到 contracts/test-vectors/commit_bundle/v2/。

覆盖：首次提交（空 parent）/带父提交（parentGitObjectIds 集合排序）/
含操作 trailer 的规范化元数据/pos-signed（确定性 seed 真实 Ed25519）；
负例：parent 全同元素重复、缺必填、treeGitObjectId 算法非 sha1、opKey
pattern 错、未知字段。

- commitBundleId = sha256(JCS(除 ID/self-digest/signature 外的完整不可变前像))[:32]；
- sourceBundleDigest 单机首提 = 空树 sha256:e3b0c44298fc1c149afbf4c8996fb92427
  ae41e4649b934ca495991b7852b855；
- pathPolicyDigest = sha256(JCS({policy:"trivial"})) 固定常量。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from app.contracts.codec import (  # noqa: E402
    build_signature_envelope,
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
)
from app.runtime.gitstager import (  # noqa: E402
    EMPTY_TREE_SHA256,
    PATH_POLICY_DIGEST,
    commit_bundle_id,
)
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "commit_bundle" / "v2"
OUT = (Path(os.environ["PI_VEC_OUT"]) / "commit_bundle" / "v2"
       if os.environ.get("PI_VEC_OUT") else OUT_DEFAULT)

SCHEMA = load_schema("commit_bundle", "2")
PROFILE = load_digest_profile("commit_bundle", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

TEST_KEY_SEED = bytes.fromhex("c01d" * 16)

EMPTY_TREE_SHA256 = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

INTENT_ID = "ff00ff00ff00ff00"
INTENT_DIGEST = "sha256:" + "1f" * 32
ATTEMPT_ID = "0022991122991122"
MANIFEST_DIGEST = "sha256:" + "2f" * 32
TREE_SHA256 = "sha256:" + "3f" * 32
TREE_GIT_ID = {"algorithm": "sha1", "hex": "4b" * 20}
PARENT = {"algorithm": "sha1", "hex": "aa" * 20}
PARENT_2 = {"algorithm": "sha1", "hex": "bb" * 20}
METADATA_DIGEST = "sha256:" + "4f" * 32
OP_KEY = "00ff" * 8
COMMIT_GIT_ID = {"algorithm": "sha1", "hex": "cc" * 20}


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(TEST_KEY_SEED)


def _sorted_parents(parents: list[dict]) -> list[dict]:
    return sorted(parents, key=lambda p: p["hex"])


def _bundle_id(**kw) -> str:
    return commit_bundle_id(
        commit_intent_id=kw["commit_intent_id"],
        commit_intent_digest=kw["commit_intent_digest"],
        attempt_id=kw["attempt_id"],
        source_bundle_digest=kw["source_bundle_digest"],
        manifest_digest=kw["manifest_digest"],
        tree_sha256=kw["tree_sha256"],
        tree_git_id=kw["tree_git_id"],
        parents=kw["parents"],
        metadata_digest=kw["metadata_digest"],
        op_key=kw["op_key"],
        commit_git_id=kw["commit_git_id"],
        path_policy_digest=kw["path_policy_digest"])


def _build(*, parents: list[dict] | None = None,
           tree_git_id: dict | None = None,
           op_key: str = OP_KEY,
           compute_digest: bool = True) -> dict:
    par = _sorted_parents(parents or [])
    bundle = {
        "contractVersion": "2",
        "workloadIdentity": "pi.commit-assembler",
        "commitBundleId": _bundle_id(
            commit_intent_id=INTENT_ID, commit_intent_digest=INTENT_DIGEST,
            attempt_id=ATTEMPT_ID, source_bundle_digest=EMPTY_TREE_SHA256,
            manifest_digest=MANIFEST_DIGEST, tree_sha256=TREE_SHA256,
            tree_git_id=tree_git_id or TREE_GIT_ID, parents=par,
            metadata_digest=METADATA_DIGEST, op_key=op_key,
            commit_git_id=COMMIT_GIT_ID, path_policy_digest=PATH_POLICY_DIGEST),
        "commitIntentId": INTENT_ID,
        "commitIntentDigest": INTENT_DIGEST,
        "selectedAttemptId": ATTEMPT_ID,
        "sourceBundleDigest": EMPTY_TREE_SHA256,
        "outputArtifactManifestDigest": MANIFEST_DIGEST,
        "proposedTreeDigest": TREE_SHA256,
        "treeGitObjectId": tree_git_id or TREE_GIT_ID,
        "parentGitObjectIds": par,
        "normalizedCommitMetadataDigest": METADATA_DIGEST,
        "operationIdempotencyKey": op_key,
        "proposedCommitGitObjectId": COMMIT_GIT_ID,
        "pathPolicyDigest": PATH_POLICY_DIGEST,
        "payloadDigest": "sha256:" + "0" * 64,
    }
    if not compute_digest:
        return bundle
    bundle["payloadDigest"] = payload_digest(bundle, PROFILE)
    return bundle


def _sign(bundle: dict) -> None:
    meta = {
        "objectType": "commit_bundle", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-git-vector",
        "issuer": "git-assembler-test",
        "issuerWorkloadIdentity": "pi.commit-assembler",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-07T08:00:00Z",
    }
    bundle["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                           "value": base64.b64encode(
                               _signing_key().sign(b"placeholder")).decode("ascii")}
    _env, sig_in, _ = build_signature_envelope(bundle, SCHEMA, PROFILE, meta)
    bundle["signature"] = {**_env, "value": base64.b64encode(
        _signing_key().sign(sig_in)).decode("ascii")}
    if os.environ.get("PI_GIT_PRINT_FP"):
        pub = _signing_key().public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        print("SK_GIT_VECTOR_FP=" + hashlib.sha256(pub).hexdigest())


def _prefill_signature(bundle: dict) -> None:
    bundle["signature"] = {
        "objectType": "commit_bundle", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-git-vector",
        "issuer": "git-assembler-test", "issuerWorkloadIdentity": "pi.commit-assembler",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-07T08:00:00Z",
        "payloadDigest": "sha256:" + "0" * 64,
        "value": base64.b64encode(_signing_key().sign(b"placeholder")).decode("ascii"),
    }


def _self_checks() -> None:
    kw = dict(commit_intent_id=INTENT_ID, commit_intent_digest=INTENT_DIGEST,
              attempt_id=ATTEMPT_ID, source_bundle_digest=EMPTY_TREE_SHA256,
              manifest_digest=MANIFEST_DIGEST, tree_sha256=TREE_SHA256,
              tree_git_id=TREE_GIT_ID, parents=[], metadata_digest=METADATA_DIGEST,
              op_key=OP_KEY, commit_git_id=COMMIT_GIT_ID,
              path_policy_digest=PATH_POLICY_DIGEST)
    assert _bundle_id(**kw) != _bundle_id(**dict(kw, parents=[PARENT])), \
        "parent 变化必须变 ID"
    assert _bundle_id(**kw) != _bundle_id(**dict(kw, op_key="ab" * 16)), \
        "操作键变化必须变 ID"
    assert _bundle_id(**kw) != _bundle_id(**dict(kw, metadata_digest="sha256:" + "9f" * 32)), \
        "提交元数据摘要变化必须变 ID"


CASES = [
    ("pos-initial-commit", True, "首次提交：空 parent + 空树基线",
     lambda: _build(), None),
    ("pos-with-parents-sorted", True, "带双父乱序传入（集合按 hex 归一化）",
     lambda: _build(parents=[PARENT_2, PARENT]), None),
    ("pos-custom-opkey", True, "专用操作键参与派生与元数据",
     lambda: _build(op_key="ab" * 16), None),
    ("pos-signed", True, "含 §9.4 签名信封（确定性测试密钥真实签名）",
     lambda: _build(parents=[PARENT], op_key="cd" * 16), None),
    ("neg-dup-parent", False, "parentGitObjectIds 全同元素重复必须拒绝",
     lambda: _build(parents=[PARENT, PARENT], compute_digest=False), "uniqueItems"),
    ("neg-missing-required", False, "缺 opKey 同层字段（operationIdempotencyKey）拒绝",
     lambda: {k: v for k, v in _build().items() if k != "operationIdempotencyKey"},
     "required"),
    ("neg-tree-algorithm", False, "treeGitObjectId 算法非 sha1 必须拒绝",
     lambda: _build(tree_git_id={"algorithm": "sha256", "hex": "4b" * 20},
                    compute_digest=False), "enum"),
    ("neg-bad-opkey-pattern", False, "operationIdempotencyKey 非 32hex 必须拒绝",
     lambda: _build(op_key="nothex", compute_digest=False), "pattern"),
    ("neg-unknown-field", False, "未知字段必须拒绝",
     lambda: _build(compute_digest=False) | {"bogus": 1}, "unknown field"),
]


def main() -> int:
    _self_checks()
    vectors = []
    for cid, valid, note, build_fn, invalid_reason in CASES:
        obj = build_fn()
        if valid:
            _sign(obj)
        else:
            _prefill_signature(obj)
        errors = sorted(VALIDATOR.iter_errors(obj), key=lambda e: str(e.path))
        schema_valid = not errors
        entry = {
            "id": cid, "kind": "positive" if valid else "negative",
            "expectedSchemaValid": valid, "note": note,
            "expectedError": invalid_reason, "object": obj,
            "calculateOnLoad": True,
        }
        if valid:
            entry["canonicalPayloadB64"] = base64.b64encode(
                canonical_payload(obj, PROFILE)).decode()
            entry["payloadDigest"] = obj["payloadDigest"]
        else:
            entry["invalidReason"] = invalid_reason
        if schema_valid != valid:
            print(f"[!!] {cid}: 期望 schemaValid={valid} 实际={schema_valid} "
                  f"{[e.message for e in errors][:2]}")
        vectors.append(entry)

    pos_ids = [v["object"]["commitBundleId"] for v in vectors
               if v["kind"] == "positive"]
    assert len(pos_ids) == len(set(pos_ids)), "正向量 commitBundleId 必须两两唯一"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "commit_bundle", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())