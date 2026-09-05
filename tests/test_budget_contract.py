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
    """pos-signed：信封 meta 重算得同一 signatureInput/payloadDigest；
    value 为固定 seed 派生密钥的真实 Ed25519 签名，公钥指纹与 registry
    （deploy/keys/keys.lock.json sk-budget-vector）一致（评审：信封身份=
    实际签名密钥，可经 registry 解析验签）。"""
    import base64 as b64
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    TEST_SEED = bytes.fromhex(
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
    REGISTRY_FP = "25db92710d26368a512531d6abb756ba8fed325fdca0241f8d881125d7c10f4d"

    v = _vec("pos-signed")
    obj = v["object"]
    meta = {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS
            if k in obj["signature"]}
    env, sig_in, _ = build_signature_envelope(obj, SCHEMA, PROFILE, meta)
    assert env["payloadDigest"] == v["payloadDigest"]
    assert obj["signature"]["payloadDigest"] == v["payloadDigest"]
    assert b64.b64encode(sig_in).decode() == v["signatureInputB64"]
    key = Ed25519PrivateKey.from_private_bytes(TEST_SEED)
    pub_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    assert hashlib.sha256(pub_der).hexdigest() == REGISTRY_FP
    key.public_key().verify(b64.b64decode(obj["signature"]["value"]), sig_in)  # 验签通过
    assert obj["signature"]["keyId"] == "sk-budget-vector"
    assert obj["signature"]["issuer"] == "ledger-test"
    with pytest.raises(Exception):
        key.public_key().verify(b64.b64decode(obj["signature"]["value"]), b"tampered")


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
    对账不符/协议违规——SENT 无 RESERVED/重复终结/SETTLED 未完整释放）均报问题。"""
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
    # 协议违规：SETTLED 未完整释放原预留（评审 fix）
    tampered4 = json.loads(json.dumps(base))
    tampered4["journal"][2]["reservedTokens"] = 0
    assert any("未完整释放预留" in p
               for p in BudgetDomain.verified_budget_grant(tampered4))
    # 协议违规：SENT 无前序 RESERVED
    tampered5 = json.loads(json.dumps(base))
    tampered5["journal"] = tampered5["journal"][1:]  # 去掉 RESERVED
    assert any("无前序 RESERVED" in p
               for p in BudgetDomain.verified_budget_grant(tampered5))
    # 协议违规：同一 invocation 重复终结
    tampered6 = json.loads(json.dumps(base))
    dup = json.loads(json.dumps(tampered6["journal"][2]))
    dup["seq"] = 4
    tampered6["journal"].append(dup)
    assert any("重复终结" in p
               for p in BudgetDomain.verified_budget_grant(tampered6))


def test_verified_combines_schema_first():
    """verified_budget_grant 先 Schema 后语义：形状损坏（缺必填）返回形状问题，
    不因语义容错而误判通过（评审 warn-fix）。"""
    base = _vec("pos-chain-settled")["object"]
    broken = json.loads(json.dumps(base))
    del broken["totalBudgetTokens"]  # Schema required 缺失
    problems = BudgetDomain.verified_budget_grant(broken)
    assert problems  # 非空
    assert not any("链断裂" in p for p in problems)  # 不落入语义误判


def test_db_flow_matches_vector_fields():
    """真实 DB 执行 reserve→sent→settle 后，journal 行与 pos-chain-settled
    向量逐字段一致（评审 fix：向量账目事实与运行时协议对照）。"""
    from app.db import connect as db_connect

    vec = _vec("pos-chain-settled")["object"]
    conn = db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                "VALUES (%s,'t','p','w','QUEUED')", (vec["taskId"],))
            conn.commit()
        b = BudgetDomain.create(conn, vec["taskId"], vec["attemptId"],
                                vec["totalBudgetTokens"], grant_id=vec["grantId"])
        conn.commit()
        inv = vec["journal"][0]["invocationId"]
        req = vec["journal"][0]["requestDigest"]
        b.reserve(conn, inv, req, vec["journal"][0]["reservedTokens"])
        conn.commit()
        b.sent(conn, inv)
        conn.commit()
        b.settle(conn, inv, 1250)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, entry_type, invocation_id, request_digest, "
                "reserved_tokens, actual_tokens, previous_entry_digest, "
                "entry_digest FROM gw_journal WHERE grant_id=%s ORDER BY seq",
                (vec["grantId"],))
            rows = cur.fetchall()
        # 契约 seq 为 per-grant 连续序号（row_number）；DB seq 为全局 BIGSERIAL，
        # 逐字段对照用行序映射 seq；其余字段与向量逐项相等
        assert [{"seq": i, "entryType": r["entry_type"],
                 "invocationId": r["invocation_id"],
                 "requestDigest": r["request_digest"],
                 "reservedTokens": r["reserved_tokens"],
                 "actualTokens": r["actual_tokens"] if r["actual_tokens"] is not None
                 else None,
                 "previousEntryDigest": r["previous_entry_digest"],
                 "entryDigest": r["entry_digest"]}
                for i, r in enumerate(rows, start=1)] == vec["journal"]
    finally:
        conn.close()


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