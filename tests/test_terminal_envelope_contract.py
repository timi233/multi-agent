"""attempt_terminal_envelope v2 契约测试（蓝图 §11.1 单机子集）：
正/负向量、digest 重算、语义校验（集合排序键唯一/self-digest/outcome↔status/
成功不得缺证据）、pos-signed 信封身份与真实签名、ID 派生唯一。"""
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
    validate,
    validate_profile_consistency,
)
from app.runtime.terminal import verified_terminal_envelope
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads(
    (ROOT / "contracts" / "test-vectors" / "attempt_terminal_envelope" / "v2"
     / "vectors.json").read_text(encoding="utf-8"))

SCHEMA = load_schema("attempt_terminal_envelope", "2")
PROFILE = load_digest_profile("attempt_terminal_envelope", "2")
VALIDATOR = Draft202012Validator(SCHEMA)
TEST_SEED = bytes.fromhex(
    "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c1c2")


def _vec(vid: str) -> dict:
    return next(x for x in VECTORS["vectors"] if x["id"] == vid)


def _base() -> dict:
    env = {
        "contractVersion": "2", "workloadIdentity": "pi.node",
        "terminalEnvelopeId": "e" * 32,
        "taskId": "0123456789abcdef", "attemptId": "1010101010101010",
        "runId": "2020202020202020", "stepIndex": 1,
        "outcomeClass": "SUCCESS_COMPLETE", "status": "SUCCESS",
        "stopReason": None,
        "runtimeObserved": {"platform": "single-node-local",
                            "reportedBy": "pi.worker",
                            "missingEvidenceReasons": []},
        "resultArtifacts": [{"path": "out/a.md", "digest": "sha256:" + "a" * 64,
                             "size": 3, "kind": "file"}],
        "unacknowledgedSideEffects": [],
        "payloadDigest": "sha256:" + "0" * 64,
    }
    env["payloadDigest"] = payload_digest(env, PROFILE)
    # signature 契约必填（评审 block-1）：placeholder 供语义层测试（不参与验签）
    from app.security import keys as node_keys
    env["signature"] = {
        "objectType": "attempt_terminal_envelope", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": node_keys.key_id(),
        "issuer": "pi.node", "issuerWorkloadIdentity": "pi.node",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-05T08:00:00Z",
        "payloadDigest": "sha256:" + "0" * 64,
        "value": node_keys.sign(b"placeholder"),
    }
    return env


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
    assert seen == 5


def test_verified_accepts_positive_vectors():
    for v in VECTORS["vectors"]:
        if v["kind"] == "positive":
            assert verified_terminal_envelope(v["object"]) == [], v["id"]


def test_verified_rejects_semantic_tamper():
    env = _base()
    assert verified_terminal_envelope(env) == []

    # 同 path 不同内容（uniqueItems 无法拒绝）→ 语义拒
    dup = dict(env)
    dup["resultArtifacts"] = [
        env["resultArtifacts"][0],
        dict(env["resultArtifacts"][0], size=99),
    ]
    assert any("重复 path" in p for p in verified_terminal_envelope(dup))

    # self-digest 篡改
    bad_self = dict(env, payloadDigest="sha256:" + "f" * 64)
    assert any("payloadDigest self" in p for p in verified_terminal_envelope(bad_self))

    # outcome↔status 搭配
    bad_map = dict(env, status="FAILED")
    assert any("搭配不一致" in p for p in verified_terminal_envelope(bad_map))

    # SUCCESS_COMPLETE 不得缺证据
    no_ev = dict(env)
    no_ev["runtimeObserved"] = {
        "platform": "single-node-local", "reportedBy": "pi.worker",
        "missingEvidenceReasons": ["node-telemetry-unavailable"]}
    no_ev["payloadDigest"] = payload_digest(no_ev, PROFILE)
    assert any("缺失证据" in p for p in verified_terminal_envelope(no_ev))

    # 失败信封允许缺失证据原因
    fail_env = dict(_base(), outcomeClass="FAILURE_PLATFORM_PROOF",
                    status="FAILED", stopReason="boom")
    fail_env["runtimeObserved"]["missingEvidenceReasons"] = ["x"]
    fail_env["payloadDigest"] = payload_digest(fail_env, PROFILE)
    assert verified_terminal_envelope(fail_env) == []


def test_verified_rejects_negative_vectors():
    for v in VECTORS["vectors"]:
        if v["kind"] == "negative":
            assert verified_terminal_envelope(v["object"]) != [], v["id"]


def test_pos_signed_envelope_identity_and_signature():
    """pos-signed：信封 meta 重算同一签名输入；value 为固定 seed 派生密钥
    的真实 Ed25519 签名；registry sk-terminal-vector 指纹一致。"""
    v = _vec("pos-signed")
    obj = v["object"]
    registry = json.loads((ROOT / "deploy" / "keys" / "keys.lock.json")
                          .read_text(encoding="utf-8"))
    reg = registry["keys"]["sk-terminal-vector"]
    assert obj["signature"]["keyId"] == "sk-terminal-vector"
    assert obj["signature"]["issuer"] == reg["issuer"] == "terminal-test"
    assert "attempt_terminal_envelopes" in reg["allowedObjectTypes"]

    meta = {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS
            if k in obj["signature"]}
    env, sig_in, _ = build_signature_envelope(obj, SCHEMA, PROFILE, meta)
    assert env["payloadDigest"] == v["payloadDigest"]
    assert base64.b64encode(sig_in).decode() == v["signatureInputB64"]

    key = Ed25519PrivateKey.from_private_bytes(TEST_SEED)
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    assert hashlib.sha256(pub_der).hexdigest() == reg["pubFingerprintSha256"]
    key.public_key().verify(base64.b64decode(obj["signature"]["value"]), sig_in)
    with pytest.raises(Exception):
        key.public_key().verify(base64.b64decode(obj["signature"]["value"]),
                               b"tampered")


def test_positive_envelope_ids_unique():
    """ID 派生覆盖不可变字段（含 stepIndex/attemptId/outcomeClass）。"""
    ids = [v["object"]["terminalEnvelopeId"]
           for v in VECTORS["vectors"] if v["kind"] == "positive"]
    assert len(ids) == len(set(ids))


def test_missing_reasons_set_normalized():
    """missingEvidenceReasons 为集合：顺序变化不改变摘要。"""
    env = _base()
    d1 = payload_digest(env, PROFILE)
    reordered = dict(env)
    reordered["runtimeObserved"] = {
        "platform": "single-node-local", "reportedBy": "pi.worker",
        "missingEvidenceReasons": ["a", "b"]}
    env2 = dict(_base())
    env2["runtimeObserved"] = {
        "platform": "single-node-local", "reportedBy": "pi.worker",
        "missingEvidenceReasons": ["b", "a"]}
    assert payload_digest(env2, PROFILE) == payload_digest(reordered, PROFILE)
    assert d1 != payload_digest(reordered, PROFILE)  # 内容变化必变