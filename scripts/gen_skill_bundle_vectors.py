# -*- coding: utf-8 -*-
"""生成 skill_bundle_snapshot v2 契约向量到
contracts/test-vectors/skill_bundle_snapshot/v2/。

覆盖：单包/双包（集合按 mountPath 排序）/含审批引用但缺个别 Decision 的
负例等；pos-signed 用确定性 seed 密钥真实 Ed25519 签名。

- skillBundleSnapshotId = sha256(JCS({bundleName, bundleRevision,
  packages(sorted by mountPath), compilerVersion, approvalSetId,
  approvalSetDigest, builtAt}))[:32]——除 ID/self-digest/signature 外的
  完整不可变前像（含审批引用：审批变化必变 ID）；
- payloadDigest 回填后经 build_signature_envelope 完成 §9.4 信封；
- PI_VEC_OUT / PI_BUNDLE_PRINT_FP 同 terminal 脚本约定。
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

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "skill_bundle_snapshot" / "v2"
OUT = (Path(os.environ["PI_VEC_OUT"]) / "skill_bundle_snapshot" / "v2"
       if os.environ.get("PI_VEC_OUT") else OUT_DEFAULT)

SCHEMA = load_schema("skill_bundle_snapshot", "2")
PROFILE = load_digest_profile("skill_bundle_snapshot", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

TEST_KEY_SEED = bytes.fromhex("b09b" * 16)

BUILT_AT = "2026-09-06T09:00:00Z"
APPROVAL_SET_ID = "aa11bb22cc33dd44"
APPROVAL_SET_DIGEST = "sha256:" + "d" * 64
_DECISION_A = "1111111111111111"
_DECISION_B = "2222222222222222"

_PKG_A = {
    "skillPackageId": "aaaa000000000001", "skillPackageVersionId": "aaaa000000000002",
    "packageName": "file-ops", "packageVersion": "1.0.0",
    "packageDigest": "sha256:" + "a1" * 32,
    "mountPath": "skills/file-ops", "entrypointPath": "SKILL.md",
}
_PKG_B = {
    "skillPackageId": "bbbb000000000001", "skillPackageVersionId": "bbbb000000000002",
    "packageName": "web-scrape", "packageVersion": "0.3.0",
    "packageDigest": "sha256:" + "b1" * 32,
    "mountPath": "skills/web-scrape", "entrypointPath": "INDEX.md",
}
_MOUNT = {
    "mountMode": "READ_ONLY", "runtimeDiscoveryMode": "STATIC_INDEX_ONLY",
    "runtimeMutationAllowed": False, "runtimeInstallAllowed": False,
    "networkRequired": False, "executableBinaryAllowed": False,
    "mcpAutoInstallAllowed": False,
}


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(TEST_KEY_SEED)


def _sorted_packages(packages: list[dict]) -> list[dict]:
    return sorted(packages, key=lambda p: p["mountPath"])


def _snap_id(bundle_name: str, revision: int, packages: list[dict],
             compiler_version: str, approval_set_id: str,
             approval_set_digest: str, built_at: str) -> str:
    blob = jcs({
        "bundleName": bundle_name, "bundleRevision": revision,
        "packages": _sorted_packages(packages),
        "compilerVersion": compiler_version,
        "approvalSetId": approval_set_id, "approvalSetDigest": approval_set_digest,
        "builtAt": built_at,
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def _build(*, bundle_name: str = "core-utils",
           packages: list[dict] | None = None,
           approval_decision_ids: list[str] | None = None,
           approval_set_digest: str = APPROVAL_SET_DIGEST,
           artifact_digest: str | None = None,
           manifest_digest: str | None = None,
           tree_digest: str | None = None,
           compute_digest: bool = True) -> dict:
    pkgs = _sorted_packages(packages or [_PKG_A])
    ad = artifact_digest or "sha256:" + "e1" * 32
    md = manifest_digest or "sha256:" + "e2" * 32
    td = tree_digest or md
    snap = {
        "contractVersion": "2",
        "workloadIdentity": "pi.skill-builder",
        "skillBundleSnapshotId": _snap_id(
            bundle_name, 1, pkgs, "0.1.0", APPROVAL_SET_ID,
            approval_set_digest, BUILT_AT),
        "bundleName": bundle_name, "bundleRevision": 1,
        "packageVersions": pkgs,
        "compilerId": "pi.skill-builder", "compilerVersion": "0.1.0",
        "bundleArtifactDigest": ad,
        "bundleManifestDigest": md,
        "expectedMountedSkillTreeDigest": td,
        "runtimeMountPolicy": dict(_MOUNT),
        "approvalSetId": APPROVAL_SET_ID,
        "approvalSetDigest": approval_set_digest,
        "approvalDecisionIds": sorted(approval_decision_ids or [_DECISION_A, _DECISION_B]),
        "builtAt": BUILT_AT,
        "payloadDigest": "sha256:" + "0" * 64,  # 占位，随后回填
    }
    if not compute_digest:
        return snap
    snap["payloadDigest"] = payload_digest(snap, PROFILE)
    return snap


def _sign(snap: dict) -> None:
    meta = {
        "objectType": "skill_bundle_snapshot", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-bundle-vector",
        "issuer": "skill-builder-test",
        "issuerWorkloadIdentity": "pi.skill-builder",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-06T09:01:00Z",
    }
    # signature 为契约必填：预置合法 placeholder 通过 Schema 后重签覆盖
    snap["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                         "value": base64.b64encode(
                             _signing_key().sign(b"placeholder")).decode("ascii")}
    _env, sig_in, _ = build_signature_envelope(snap, SCHEMA, PROFILE, meta)
    snap["signature"] = {**_env, "value": base64.b64encode(
        _signing_key().sign(sig_in)).decode("ascii")}
    if os.environ.get("PI_BUNDLE_PRINT_FP"):
        pub = _signing_key().public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        print("SK_BUNDLE_VECTOR_FP=" + hashlib.sha256(pub).hexdigest())


def _prefill_signature(snap: dict) -> None:
    snap["signature"] = {
        "objectType": "skill_bundle_snapshot", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-bundle-vector",
        "issuer": "skill-builder-test", "issuerWorkloadIdentity": "pi.skill-builder",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": BUILT_AT,
        "payloadDigest": "sha256:" + "0" * 64,
        "value": base64.b64encode(_signing_key().sign(b"placeholder")).decode("ascii"),
    }


def _self_checks() -> None:
    kw = dict(bundle_name="core-utils", revision=1, packages=[_PKG_A],
              compiler_version="0.1.0", approval_set_id=APPROVAL_SET_ID,
              approval_set_digest=APPROVAL_SET_DIGEST, built_at=BUILT_AT)
    assert _snap_id(**kw) != _snap_id(**dict(kw, revision=2)), "revision 变化必须变 ID"
    assert _snap_id(**kw) != _snap_id(**dict(kw, packages=[_PKG_B])), \
        "选包变化必须变 ID"
    assert _snap_id(**kw) != _snap_id(**dict(kw, approval_set_id="9999999999999999")), \
        "审批引用变化必须变 ID"


CASES = [
    ("pos-single-package", True, "单包 + 完整双 Decision 审批引用",
     lambda: _build(), None),
    ("pos-two-packages-sorted", True, "双包乱序传入（集合按 mountPath 归一化）",
     lambda: _build(packages=[_PKG_B, _PKG_A]), None),
    ("pos-mount-policy-frozen", True, "runtimeMountPolicy 固定首期只读静态（不同 bundle 名区分 ID）",
     lambda: _build(bundle_name="ro-static"), None),
    ("pos-signed", True, "含 §9.4 签名信封（确定性测试密钥真实签名；不同审批摘要避免与双包向量同 ID）",
     lambda: _build(packages=[_PKG_B, _PKG_A],
                    approval_decision_ids=[_DECISION_B, _DECISION_A],
                    approval_set_digest="sha256:" + "d1" * 32), None),
    ("neg-missing-required", False, "缺 bundleName 必须拒绝",
     lambda: {k: v for k, v in _build().items() if k != "bundleName"}, "required"),
    ("neg-dup-mount-path", False, "packageVersions 同 mountPath 全同元素拒绝",
     lambda: _build(packages=[_PKG_A, _PKG_A], compute_digest=False), "uniqueItems"),
    ("neg-bad-pkg-digest", False, "packageDigest 非 sha256:64hex 必须拒绝",
     lambda: _build(packages=[dict(_PKG_A, packageDigest="sha256:zz")],
                    compute_digest=False), "pattern"),
    ("neg-dup-decision-id", False, "approvalDecisionIds 全同元素重复必须拒绝",
     lambda: _build(approval_decision_ids=[_DECISION_A, _DECISION_A],
                    compute_digest=False), "uniqueItems"),
    ("neg-unknown-field", False, "未知字段必须拒绝",
     lambda: _build(compute_digest=False) | {"bogusField": 1}, "unknown field"),
    ("neg-mount-policy-tamper", False, "mountMode 篡改（非 READ_ONLY）必须拒绝",
     lambda: dict(_build(compute_digest=False),
                  runtimeMountPolicy={**_MOUNT, "mountMode": "WRITE"}), "enum"),
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

    pos_ids = [v["object"]["skillBundleSnapshotId"] for v in vectors
               if v["kind"] == "positive"]
    assert len(pos_ids) == len(set(pos_ids)), \
        "正向量 skillBundleSnapshotId 必须两两唯一"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "skill_bundle_snapshot", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())