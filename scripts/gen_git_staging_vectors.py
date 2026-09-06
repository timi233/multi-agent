# -*- coding: utf-8 -*-
"""生成 git_staging_result v2 契约向量到
contracts/test-vectors/git_staging_result/v2/。

覆盖：首提（expectedRef=null）/追加提交（expectedRef=上一读回）/乱序不适用
（无集合字段）/pos-signed（确定性 seed 真实 Ed25519）；负例：expectedRef
算法非 sha1、缺必填、pattern 错（candidateRef/opKey）、未知字段。

gitStagingResultId = sha256(JCS(除 ID/self-digest/signature 外的完整不可变
前像（含 expectedRef/appliedCommit/epochs/opKey，不含 stagedAt）))[:32]。
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
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
)
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "git_staging_result" / "v2"
OUT = (Path(os.environ["PI_VEC_OUT"]) / "git_staging_result" / "v2"
       if os.environ.get("PI_VEC_OUT") else OUT_DEFAULT)

SCHEMA = load_schema("git_staging_result", "2")
PROFILE = load_digest_profile("git_staging_result", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

TEST_KEY_SEED = bytes.fromhex("9a11" * 16)

BUNDLE_ID = "abcd1234abcd1234abcd1234abcd1234"
BUNDLE_DIGEST = "sha256:" + "5e" * 32
REPO_ID = "00aa11bb22cc33dd"
CANDIDATE_REF = "refs/heads/main"
EXPECTED_NULL = None
EXPECTED = {"algorithm": "sha1", "hex": "ab" * 20}
APPLIED = {"algorithm": "sha1", "hex": "cd" * 20}
OP_KEY = "1122" * 8
STAGED_AT = "2026-09-07T10:00:00Z"


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(TEST_KEY_SEED)


def _result_id(**kw) -> str:
    blob = jcs({
        "commitBundleId": kw["bundle_id"],
        "commitBundleDigest": kw["bundle_digest"],
        "repositoryId": kw["repo_id"],
        "candidateRef": kw["ref"],
        "expectedRefGitObjectId": kw["expected"],
        "appliedCommitGitObjectId": kw["applied"],
        "controlPlaneEpoch": 0,
        "gitStagingEpoch": kw["epoch"],
        "revocationEpoch": 0,
        "operationIdempotencyKey": kw["op_key"],
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def _build(*, expected=None, epoch: int = 1, op_key: str = OP_KEY,
           bundled_id: str = BUNDLE_ID, compute_digest: bool = True) -> dict:
    result = {
        "contractVersion": "2",
        "workloadIdentity": "pi.git-stager",
        "gitStagingResultId": _result_id(
            bundle_id=bundled_id, bundle_digest=BUNDLE_DIGEST, repo_id=REPO_ID,
            ref=CANDIDATE_REF, expected=expected, applied=APPLIED,
            epoch=epoch, op_key=op_key),
        "commitBundleId": bundled_id,
        "commitBundleDigest": BUNDLE_DIGEST,
        "repositoryId": REPO_ID,
        "candidateRef": CANDIDATE_REF,
        "expectedRefGitObjectId": expected,
        "appliedCommitGitObjectId": APPLIED,
        "controlPlaneEpoch": 0,
        "gitStagingEpoch": epoch,
        "revocationEpoch": 0,
        "operationIdempotencyKey": op_key,
        "stagedAt": STAGED_AT,
        "payloadDigest": "sha256:" + "0" * 64,
    }
    if not compute_digest:
        return result
    result["payloadDigest"] = payload_digest(result, PROFILE)
    return result


def _sign(result: dict) -> None:
    meta = {
        "objectType": "git_staging_result", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-git-staging-vector",
        "issuer": "git-stager-test",
        "issuerWorkloadIdentity": "pi.git-stager",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-07T10:01:00Z",
    }
    result["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                           "value": base64.b64encode(
                               _signing_key().sign(b"placeholder")).decode("ascii")}
    _env, sig_in, _ = build_signature_envelope(result, SCHEMA, PROFILE, meta)
    result["signature"] = {**_env, "value": base64.b64encode(
        _signing_key().sign(sig_in)).decode("ascii")}
    if os.environ.get("PI_GITSTAGING_PRINT_FP"):
        pub = _signing_key().public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        print("SK_GIT_STAGING_VECTOR_FP=" + hashlib.sha256(pub).hexdigest())


def _prefill_signature(result: dict) -> None:
    result["signature"] = {
        "objectType": "git_staging_result", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-git-staging-vector",
        "issuer": "git-stager-test", "issuerWorkloadIdentity": "pi.git-stager",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": STAGED_AT,
        "payloadDigest": "sha256:" + "0" * 64,
        "value": base64.b64encode(_signing_key().sign(b"placeholder")).decode("ascii"),
    }


def _self_checks() -> None:
    kw = dict(bundle_id=BUNDLE_ID, bundle_digest=BUNDLE_DIGEST, repo_id=REPO_ID,
              ref=CANDIDATE_REF, expected=None, applied=APPLIED, epoch=1,
              op_key=OP_KEY)
    assert _result_id(**kw) != _result_id(**dict(kw, expected=EXPECTED)), \
        "expectedRef 变化必须变 ID"
    assert _result_id(**kw) != _result_id(**dict(kw, epoch=2)), "epoch 变化必须变 ID"
    assert _result_id(**kw) != _result_id(**dict(kw, op_key="ff" * 16)), \
        "操作键变化必须变 ID"
    assert _result_id(**kw) == _result_id(**dict(kw, expected=None)), \
        "stagedAt 不进 ID（幂等：同数据重试 ID 稳定）"


CASES = [
    ("pos-initial-staging", True, "首提：expectedRef=null（ref 必须不存在）",
     lambda: _build(expected=None), None),
    ("pos-append-staging", True, "追加提交：expectedRef=上一读回",
     lambda: _build(expected=EXPECTED, epoch=2), None),
    ("pos-distinct-opkey", True, "专用操作键（幂等键参与派生）",
     lambda: _build(expected=EXPECTED, epoch=3, op_key="99aa" * 8), None),
    ("pos-signed", True, "含 §9.4 签名信封（确定性测试密钥真实签名）",
     lambda: _build(expected=None, op_key="bbcc" * 8), None),
    ("neg-expected-algorithm", False, "expectedRef 算法非 sha1 必须拒绝",
     lambda: _build(expected={"algorithm": "sha256", "hex": "ab" * 20},
                    compute_digest=False), "enum"),
    ("neg-missing-required", False, "缺 candidateRef 字段必须拒绝",
     lambda: {k: v for k, v in _build().items() if k != "candidateRef"}, "required"),
    ("neg-bad-ref-pattern", False, "candidateRef 非 refs/heads/ 前缀必须拒绝",
     lambda: _build(compute_digest=False) | {"candidateRef": "not-ref"}, "pattern"),
    ("neg-bad-opkey", False, "operationIdempotencyKey 非 32hex 必须拒绝",
     lambda: _build(op_key="x" * 3, compute_digest=False), "pattern"),
    ("neg-unknown-field", False, "未知字段必须拒绝",
     lambda: _build(compute_digest=False) | {"bogus": True}, "unknown field"),
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

    pos_ids = [v["object"]["gitStagingResultId"] for v in vectors
               if v["kind"] == "positive"]
    assert len(pos_ids) == len(set(pos_ids)), "正向量 gitStagingResultId 必须两两唯一"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "git_staging_result", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())