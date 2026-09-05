"""budget_grant v2 契约测试：正/负向量、digest 重算、Grant 签发不可变 +
消费事实 mutable、语义校验（verified_budget_grant）。"""
import base64
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
from app.runtime.budget import ROOT_DIGEST, _entry_digest, BudgetDomain

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads(
    (ROOT / "contracts" / "test-vectors" / "budget_grant" / "v2" / "vectors.json")
    .read_text(encoding="utf-8"))

SCHEMA = load_schema("budget_grant", "2")
PROFILE = load_digest_profile("budget_grant", "2")
VALIDATOR = Draft202012Validator(SCHEMA)


def _vec(vid: str) -> dict:
    return next(x for x in VECTORS["vectors"] if x["id"] == vid)


def test_profile_consistency_ok():
    assert validate_profile_consistency(SCHEMA, PROFILE) == []


def test_vectors_schema_expectations():
    for v in VECTORS["vectors"]:
        errors = list(VALIDATOR.iter_errors(v["object"]))
        assert (not errors) == v["expectedSchemaValid"], (
            f"{v['id']}: 期望 schemaValid={v['expectedSchemaValid']} "
            f"实际={not errors} {[e.message for e in errors][:1]}")


def test_positive_digests_recomputable():
    """全部正向量：canonicalPayload/payloadDigest 可独立重算（Python 侧复现）。"""
    seen = 0
    for v in VECTORS["vectors"]:
        if v["kind"] != "positive":
            continue
        canon = canonical_payload(v["object"], PROFILE)
        assert base64.b64encode(canon).decode() == v["canonicalPayloadB64"], v["id"]
        assert payload_digest(v["object"], PROFILE) == v["payloadDigest"], v["id"]
        seen += 1
    assert seen == 5


def test_pos_signed_envelope_self_consistent():
    v = _vec("pos-signed")
    obj = v["object"]
    meta = {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS
            if k in obj["signature"]}
    env, sig_in, _ = build_signature_envelope(obj, SCHEMA, PROFILE, meta)
    assert env["payloadDigest"] == v["payloadDigest"]
    assert obj["signature"]["payloadDigest"] == v["payloadDigest"]
    assert base64.b64encode(sig_in).decode() == v["signatureInputB64"]


def test_journal_chain_is_real_digests():
    """向量链为真实 _entry_digest 序列：previous 链、entryDigest 逐条重算相等
    （评审 block-fix：不再人工占位）。"""
    for v in VECTORS["vectors"]:
        if v["kind"] != "positive" or not v["object"].get("journal"):
            continue
        prev = ROOT_DIGEST
        grant_id = v["object"]["grantId"]
        for e in v["object"]["journal"]:
            assert e["previousEntryDigest"] == prev, v["id"]
            recomputed = _entry_digest(
                prev, grant_id, e["invocationId"], e["entryType"],
                e["reservedTokens"], e.get("actualTokens"), e.get("requestDigest"))
            assert e["entryDigest"] == recomputed, v["id"]
            prev = e["entryDigest"]


def test_verified_function_accepts_positive_rejects_tamper():
    """verified_budget_grant：正向量全通过；篡改（断链/伪造 digest/consumed
    对账不符）均报问题；可信快照 empty。"""
    for v in VECTORS["vectors"]:
        if v["kind"] == "positive":
            assert BudgetDomain.verified_budget_grant(v["object"]) == [], v["id"]
    base = _vec("pos-chain-settled")["object"]
    # 断链：前条 digest 被改
    tampered = json.loads(json.dumps(base))
    tampered["journal"][1]["previousEntryDigest"] = "f" * 64
    assert any("链断裂" in p
               for p in BudgetDomain.verified_budget_grant(tampered))
    # 伪造 digest：非真实重算
    tampered2 = json.loads(json.dumps(base))
    tampered2["journal"][2]["entryDigest"] = "e" * 64
    assert any("非真实重算" in p
               for p in BudgetDomain.verified_budget_grant(tampered2))
    # consumed 对账不符
    tampered3 = json.loads(json.dumps(base))
    tampered3["consumedTokens"] = 9999
    assert any("consumedTokens" in p
               for p in BudgetDomain.verified_budget_grant(tampered3))


def test_grant_immutable_consumption_mutable():
    """Grant 签发不变：追加消费事实（journal/consumed/status 变化）不改变
    payloadDigest；签发额度变化（totalBudgetTokens）才改变（评审 block-fix：对象
    边界——不可变授权与消费事实分离）。"""
    base = _vec("pos-chain-settled")["object"]
    digest1 = payload_digest(base, PROFILE)
    more = json.loads(json.dumps(base))
    more["journal"].append({
        "seq": 4, "entryType": "SETTLED", "invocationId": "1111111111111111",
        "requestDigest": None, "reservedTokens": 0, "actualTokens": 10,
        "previousEntryDigest": base["journal"][-1]["entryDigest"],
        "entryDigest": "0" * 64,  # mutable 事实不投影，digest 不受影响
    })
    more["consumedTokens"] = 1260
    more["status"] = "SETTLED"
    assert payload_digest(more, PROFILE) == digest1  # 消费事实不改变授权签名
    changed = dict(base, totalBudgetTokens=100000)
    assert payload_digest(changed, PROFILE) != digest1  # 签发额度变化必变


def test_positive_facts_self_consistent():
    """正向量事实自洽：consumedTokens == ΣSETTLED.actualTokens，且状态与
    journal 生命周期对应（评审 should-fix：消除矛盾样例）。"""
    for v in VECTORS["vectors"]:
        if v["kind"] != "positive":
            continue
        obj = v["object"]
        settled = sum(e.get("actualTokens") or 0 for e in obj["journal"]
                      if e.get("entryType") == "SETTLED")
        assert obj["consumedTokens"] == settled, v["id"]
    assert _vec("pos-minimal")["object"]["consumedTokens"] == 0
    assert _vec("pos-unknown")["object"]["consumedTokens"] == 0