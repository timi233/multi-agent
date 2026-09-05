"""execution_plan_snapshot v2 契约测试：正/负向量、digest 重算、§10.5.1
不可变计划语义（verified_execution_plan：广播 digest/self-digest/INITIAL
parent=null/上游引用无悬垂）、pos-signed 信封身份与真实签名。"""
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
from app.runtime.plans import _inputs_digest, verified_execution_plan
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads(
    (ROOT / "contracts" / "test-vectors" / "execution_plan_snapshot" / "v2"
     / "vectors.json").read_text(encoding="utf-8"))

SCHEMA = load_schema("execution_plan_snapshot", "2")
PROFILE = load_digest_profile("execution_plan_snapshot", "2")
VALIDATOR = Draft202012Validator(SCHEMA)
TEST_SEED = bytes.fromhex(
    "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1")


def _vec(vid: str) -> dict:
    return next(x for x in VECTORS["vectors"] if x["id"] == vid)


def _base_plan() -> dict:
    plan = {
        "contractVersion": "2",
        "workloadIdentity": "pi.orchestrator",
        "executionPlanSnapshotId": "e" * 32,
        "taskId": "0123456789abcdef",
        "compilerId": "pi-orchestrator",
        "compilerVersion": "0.1.0",
        "planKind": "INITIAL",
        "plannedAttemptInputs": [{
            "plannedAttemptInputId": "1010101010101010",
            "workflowNodeId": "step-1",
            "runKind": "IMPLEMENTATION",
            "deliverableKind": "CODE_CHANGE",
            "promptContent": "写 work.md",
        }],
        "parentExecutionPlanSnapshotId": None,
        "compiledBy": "pi-orchestrator@0.1.0",
        "compiledAt": "2026-09-05T08:00:00Z",
        "payloadDigest": "sha256:" + "0" * 64,
    }
    plan["canonicalPlannedInputsDigest"] = _inputs_digest(plan)
    plan["payloadDigest"] = payload_digest(plan, PROFILE)
    return plan


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


def test_inputs_digest_is_set_normalized():
    """集合归一化：多元素逆序等价、内容变化必变（CT 集合语义）。"""
    plan = _base_plan()
    two = dict(plan, plannedAttemptInputs=[
        plan["plannedAttemptInputs"][0],
        {
            "plannedAttemptInputId": "2020202020202020",
            "workflowNodeId": "step-2",
            "runKind": "READ_ONLY",
            "deliverableKind": "READ_ONLY_EVIDENCE",
            "promptContent": "验收",
        },
    ])
    d1 = _inputs_digest(two)
    assert two["canonicalPlannedInputsDigest"] != d1  # 单步计划摘要与双步不同
    swapped = dict(two, plannedAttemptInputs=list(
        reversed(two["plannedAttemptInputs"])))
    assert _inputs_digest(swapped) == d1  # 逆序等价
    changed = dict(two)
    changed["plannedAttemptInputs"] = [
        dict(i, promptContent=i["promptContent"] + "!") for i in
        two["plannedAttemptInputs"]]
    assert _inputs_digest(changed) != d1  # 内容变化必变


def test_verified_rejects_duplicate_input_id():
    """评审 block-2：同 plannedAttemptInputId 不同内容（uniqueItems 无法
    拒绝）必须被 verified_execution_plan 拒收，且不抛异常。"""
    plan = _base_plan()
    dup = dict(plan)
    dup["plannedAttemptInputs"] = [
        plan["plannedAttemptInputs"][0],
        dict(plan["plannedAttemptInputs"][0], promptContent="不同内容"),
    ]
    problems = verified_execution_plan(dup)
    assert any("重复 plannedAttemptInputId" in p for p in problems)


def test_schema_rejects_repair_kind():
    """评审 block-3：Repair 契约字段未落地前，Schema 收紧 planKind 仅
    INITIAL，REPAIR 一律拒收（不保留静默后门）。"""
    from jsonschema import Draft202012Validator as V
    p = dict(_base_plan(), planKind="REPAIR")
    errors = [e.message for e in V(SCHEMA).iter_errors(p)]
    assert any("INITIAL" in e for e in errors)


def test_positive_vector_ids_unique():
    """评审 block-1 回归：executionPlanSnapshotId 派生覆盖全部不可变字段，
    正向量 ID 两两唯一（taskSpecDigest 参与派生）。"""
    ids = [v["object"]["executionPlanSnapshotId"]
           for v in VECTORS["vectors"] if v["kind"] == "positive"]
    assert len(ids) == len(set(ids))
    assert _vec("pos-single-step")["object"]["executionPlanSnapshotId"] != \
        _vec("pos-with-spec-parent-null")["object"]["executionPlanSnapshotId"]


def test_verified_accepts_positive_vectors():
    for v in VECTORS["vectors"]:
        if v["kind"] == "positive":
            assert verified_execution_plan(v["object"]) == [], v["id"]


def test_verified_rejects_semantic_tamper():
    p = _base_plan()
    assert verified_execution_plan(p) == []

    tampered = dict(p, canonicalPlannedInputsDigest="sha256:" + "f" * 64)
    problems = verified_execution_plan(tampered)
    assert any("canonicalPlannedInputsDigest" in x for x in problems)

    bad_parent = dict(p, parentExecutionPlanSnapshotId="d" * 32)
    problems = verified_execution_plan(bad_parent)
    assert any("INITIAL" in x for x in problems)

    dangling = dict(p)
    dangling["plannedAttemptInputs"] = [dict(
        p["plannedAttemptInputs"][0],
        upstreamBindings=[{"slotId": "s", "producerNodeId": "step-9",
                           "required": True}])]
    problems = verified_execution_plan(dangling)
    assert any("悬垂" in x for x in problems)

    bad_self = dict(p, payloadDigest="sha256:" + "e" * 64)
    assert any("payloadDigest self" in x for x in verified_execution_plan(bad_self))


def test_verified_rejects_negative_vectors():
    """负向量即使形状非法也须返回非空问题（形状先于语义，不误判为合法）。"""
    for v in VECTORS["vectors"]:
        if v["kind"] == "negative":
            assert verified_execution_plan(v["object"]) != [], v["id"]


def test_pos_signed_envelope_identity_and_signature():
    """pos-signed：信封 meta 重算同一签名输入；value 为固定 seed 派生密钥
    的真实 Ed25519 签名；注册的 sk-orchestrator-vector 公钥指纹与信封
    keyId/issuer 一致且验签通过。"""
    v = _vec("pos-signed")
    obj = v["object"]
    registry = json.loads((ROOT / "deploy" / "keys" / "keys.lock.json")
                          .read_text(encoding="utf-8"))
    reg = registry["keys"]["sk-orchestrator-vector"]
    assert obj["signature"]["keyId"] == "sk-orchestrator-vector"
    assert obj["signature"]["issuer"] == reg["issuer"] == "orchestrator-test"
    assert obj["signature"]["issuerWorkloadIdentity"] == "pi.orchestrator"
    assert "execution_plan_snapshots" in reg["allowedObjectTypes"]

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


def test_schema_validate_reports_all_errors():
    """validate 聚合全部形状问题而不只首条。"""
    bad = _base_plan()
    bad["runKind"] = "SCRIPT"  # 非法顶层字段
    problems = validate(bad, SCHEMA)
    assert any("additionalProperties" in p or "runKind" in p for p in problems)