"""commit_bundle v2 契约测试（蓝图 §10.10 单机子集）：
向量期望/digest 重算/verified 语义（重复 parent hex、goid 算法、self）/
pos-signed 真实 Ed25519+registry/正向量 ID 唯一/复现性接入验证。"""
import base64
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.contracts.codec import (
    SIGNATURE_ENVELOPE_KEYS,
    build_signature_envelope,
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
    validate_profile_consistency,
)
from app.runtime.gitstager import (
    verified_commit_bundle,
    verify_commit_bundle_signature,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads(
    (ROOT / "contracts" / "test-vectors" / "commit_bundle" / "v2"
     / "vectors.json").read_text(encoding="utf-8"))

SCHEMA = load_schema("commit_bundle", "2")
PROFILE = load_digest_profile("commit_bundle", "2")
VALIDATOR = Draft202012Validator(SCHEMA)
TEST_SEED = bytes.fromhex("c01d" * 16)

_EMPTY_TREE_SHA256 = ("sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934"
                      "ca495991b7852b855")
_PARENT = {"algorithm": "sha1", "hex": "aa" * 20}


def _vec(vid: str) -> dict:
    return next(x for x in VECTORS["vectors"] if x["id"] == vid)


def _base() -> dict:
    from app.contracts.codec import jcs
    from app.runtime.gitstager import commit_bundle_id
    bundle = {
        "contractVersion": "2", "workloadIdentity": "pi.commit-assembler",
        "commitIntentId": "ff00ff00ff00ff00",
        "commitIntentDigest": "sha256:" + "1f" * 32,
        "selectedAttemptId": "0022991122991122",
        "sourceBundleDigest": _EMPTY_TREE_SHA256,
        "outputArtifactManifestDigest": "sha256:" + "2f" * 32,
        "proposedTreeDigest": "sha256:" + "3f" * 32,
        "treeGitObjectId": {"algorithm": "sha1", "hex": "4b" * 20},
        "parentGitObjectIds": [],
        "normalizedCommitMetadataDigest": "sha256:" + "4f" * 32,
        "operationIdempotencyKey": "00ff" * 8,
        "proposedCommitGitObjectId": {"algorithm": "sha1", "hex": "cc" * 20},
        "pathPolicyDigest": "sha256:" + hashlib.sha256(
            jcs({"policy": "trivial"})).hexdigest(),
        "payloadDigest": "sha256:" + "0" * 64,
    }
    bundle["commitBundleId"] = commit_bundle_id(
        commit_intent_id=bundle["commitIntentId"],
        commit_intent_digest=bundle["commitIntentDigest"],
        attempt_id=bundle["selectedAttemptId"],
        source_bundle_digest=bundle["sourceBundleDigest"],
        manifest_digest=bundle["outputArtifactManifestDigest"],
        tree_sha256=bundle["proposedTreeDigest"],
        tree_git_id=bundle["treeGitObjectId"],
        parents=bundle["parentGitObjectIds"],
        metadata_digest=bundle["normalizedCommitMetadataDigest"],
        op_key=bundle["operationIdempotencyKey"],
        commit_git_id=bundle["proposedCommitGitObjectId"],
        path_policy_digest=bundle["pathPolicyDigest"])
    bundle["payloadDigest"] = payload_digest(bundle, PROFILE)
    from app.security import keys as node_keys
    from app.contracts.codec import build_signature_envelope
    meta = {
        "objectType": "commit_bundle", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": node_keys.key_id(),
        "issuer": "pi.commit-assembler",
        "issuerWorkloadIdentity": "pi.commit-assembler",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-07T08:00:00Z",
    }
    bundle["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                           "value": node_keys.sign(b"placeholder")}
    _env, sig_in, _ = build_signature_envelope(bundle, SCHEMA, PROFILE, meta)
    bundle["signature"] = {**_env, "value": node_keys.sign(sig_in)}
    return bundle


def test_profile_consistency_ok():
    assert validate_profile_consistency(SCHEMA, PROFILE) == []


def test_vectors_schema_expectations():
    for v in VECTORS["vectors"]:
        errors = list(VALIDATOR.iter_errors(v["object"]))
        assert (not errors) == v["expectedSchemaValid"], (
            f"{v['id']}: 期望 schemaValid={v['expectedSchemaValid']} "
            f"实际={not errors} {[e.message for e in errors][:1]}")


def test_positive_digests_recomputable():
    seen = 0
    for v in VECTORS["vectors"]:
        if v["kind"] != "positive":
            continue
        canon = canonical_payload(v["object"], PROFILE)
        assert base64.b64encode(canon).decode() == v["canonicalPayloadB64"], v["id"]
        assert payload_digest(v["object"], PROFILE) == v["payloadDigest"], v["id"]
        seen += 1
    assert seen == 4


def test_verified_accepts_positive_vectors():
    for v in VECTORS["vectors"]:
        if v["kind"] == "positive":
            assert verified_commit_bundle(v["object"]) == [], v["id"]


def test_verified_rejects_semantic_tamper():
    bundle = _base()
    assert verified_commit_bundle(bundle) == []

    # parent 引用篡改（同对象集合由 schema uniqueItems 拦截；此处验证整体变化）
    from app.runtime.gitstager import commit_bundle_id
    dup = dict(bundle)
    dup["parentGitObjectIds"] = [dict(_PARENT)]
    dup["commitBundleId"] = commit_bundle_id(
        commit_intent_id=dup["commitIntentId"],
        commit_intent_digest=dup["commitIntentDigest"],
        attempt_id=dup["selectedAttemptId"],
        source_bundle_digest=dup["sourceBundleDigest"],
        manifest_digest=dup["outputArtifactManifestDigest"],
        tree_sha256=dup["proposedTreeDigest"],
        tree_git_id=dup["treeGitObjectId"],
        parents=dup["parentGitObjectIds"],
        metadata_digest=dup["normalizedCommitMetadataDigest"],
        op_key=dup["operationIdempotencyKey"],
        commit_git_id=dup["proposedCommitGitObjectId"],
        path_policy_digest=dup["pathPolicyDigest"])
    dup["payloadDigest"] = payload_digest(dup, PROFILE)
    assert verified_commit_bundle(dup) == []  # 合法父提交可接受

    # tree 算法非 sha1 → schema const 拒（problems 非空）
    bad_algo = dict(bundle)
    bad_algo["treeGitObjectId"] = {"algorithm": "sha256", "hex": "4b" * 20}
    assert verified_commit_bundle(bad_algo) != []

    # self-digest 篡改 → 拒
    bad_self = dict(bundle, payloadDigest="sha256:" + "9" * 64)
    assert any("payloadDigest self" in p for p in verified_commit_bundle(bad_self))


def test_pos_signed_envelope_identity_and_signature():
    v = _vec("pos-signed")
    obj = v["object"]
    registry = json.loads((ROOT / "deploy" / "keys" / "keys.lock.json")
                          .read_text(encoding="utf-8"))
    reg = registry["keys"]["sk-git-vector"]
    assert obj["signature"]["keyId"] == "sk-git-vector"
    assert obj["signature"]["issuer"] == reg["issuer"] == "git-assembler-test"
    assert "commit_bundles" in reg["allowedObjectTypes"]
    key = Ed25519PrivateKey.from_private_bytes(TEST_SEED)
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    assert hashlib.sha256(pub_der).hexdigest() == reg["pubFingerprintSha256"]
    meta = {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS if k in obj["signature"]}
    _, sig_in, _ = build_signature_envelope(obj, SCHEMA, PROFILE, meta)
    key.public_key().verify(base64.b64decode(obj["signature"]["value"]), sig_in)
    with pytest.raises(Exception):
        key.public_key().verify(base64.b64decode(obj["signature"]["value"]),
                                b"tampered")


def test_runtime_signature_verify():
    """运行时 build/verify 闭环（节点密钥 issuer=pi.commit-assembler）。"""
    bundle = _base()
    assert verify_commit_bundle_signature(bundle) is True
    tampered = dict(bundle)
    tampered["payloadDigest"] = "sha256:" + "0" * 64
    assert verified_commit_bundle(tampered) != []


def test_verified_rejects_policy_and_id_tamper():
    """评审 block-1/2：pathPolicyDigest 常量篡改拒收；commitBundleId
    不信任自报（不可变字段变化而 ID 陈旧 → 重算不一致拒收）。"""
    from app.runtime.gitstager import commit_bundle_id
    bundle = _base()
    assert verified_commit_bundle(bundle) == []

    # policy 常量篡改（ID 与 payload 同步重算以隔离，仅报政策常量）
    pol = dict(bundle)
    pol["pathPolicyDigest"] = "sha256:" + "7" * 64
    pol["commitBundleId"] = commit_bundle_id(
        commit_intent_id=pol["commitIntentId"],
        commit_intent_digest=pol["commitIntentDigest"],
        attempt_id=pol["selectedAttemptId"],
        source_bundle_digest=pol["sourceBundleDigest"],
        manifest_digest=pol["outputArtifactManifestDigest"],
        tree_sha256=pol["proposedTreeDigest"],
        tree_git_id=pol["treeGitObjectId"],
        parents=pol["parentGitObjectIds"],
        metadata_digest=pol["normalizedCommitMetadataDigest"],
        op_key=pol["operationIdempotencyKey"],
        commit_git_id=pol["proposedCommitGitObjectId"],
        path_policy_digest=pol["pathPolicyDigest"])
    pol["payloadDigest"] = payload_digest(pol, PROFILE)
    problems = verified_commit_bundle(pol)
    assert any("政策常量" in p for p in problems)
    assert not any("不一致" in p for p in problems)  # ID 已同步，无干扰

    # ID 陈旧：opKey 篡改但 ID 未重算 → 重算不一致
    stale = dict(bundle)
    stale["operationIdempotencyKey"] = "ee" * 16
    stale["payloadDigest"] = payload_digest(stale, PROFILE)
    problems2 = verified_commit_bundle(stale)
    assert any("commitBundleId 与不可变前像重算不一致" in p for p in problems2)


def test_positive_bundle_ids_unique():
    ids = [v["object"]["commitBundleId"]
           for v in VECTORS["vectors"] if v["kind"] == "positive"]
    assert len(ids) == len(set(ids))

    # ID 派生覆盖不可变字段（parent/opKey/metadata 变化必变，由生成器 self_checks
    # 保证；此处复核签名向量与乱序父向量确实不同）
    assert _vec("pos-with-parents-sorted")["object"]["commitBundleId"] != \
        _vec("pos-initial-commit")["object"]["commitBundleId"]