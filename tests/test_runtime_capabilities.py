"""Runtime 能力报告（RT）测试：结构/签名重算/确定性/幂等/API 端点。"""
import hashlib

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.contracts import canonical_payload, load_digest_profile, load_schema
from app.main import create_app
from app.runtime.capabilities import (
    RT_KNOWN_GAPS,
    build_cached_report,
    build_report,
    clear_cache,
    core_facts,
)
from app.runtime.tools import TOOL_DEFINITIONS

SCHEMA = load_schema("runtime_capability_report", "2")
PROFILE = load_digest_profile("runtime_capability_report", "2")


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _tool_names() -> set[str]:
    return {t["function"]["name"] for t in TOOL_DEFINITIONS}


def test_report_structure():
    rep = build_report()
    required = {"contractId", "contractVersion", "workloadIdentity",
                "runtimeType", "runtimeVersion", "model", "resourceDefaults",
                "isolation", "toolCapabilities", "knownGaps", "generatedAt",
                "signature"}
    assert required <= set(rep)
    assert rep["workloadIdentity"] == "pi.node"
    assert rep["runtimeType"] == "pi-single-node"
    assert len(rep["contractId"]) == 32
    assert {t["name"] for t in rep["toolCapabilities"]} == _tool_names()
    assert rep["isolation"]["processExecutionForTools"] is True
    assert rep["knownGaps"] == RT_KNOWN_GAPS  # 事实基线如实披露


def test_schema_and_profile_consistency():
    from app.contracts.codec import validate_profile_consistency

    assert validate_profile_consistency(SCHEMA, PROFILE) == []
    errors = list(Draft202012Validator(SCHEMA).iter_errors(build_report()))
    assert errors == []


def test_signature_envelope_digest_recomputable():
    """信封 payloadDigest == 独立重算 canonicalPayload 的 sha256。"""
    rep = build_report()
    sig = rep["signature"]
    envelope_keys = {k for k in sig if k != "value"}  # value=Ed25519 占位
    assert envelope_keys == {
        "objectType", "schemaVersion", "signatureAlgorithm", "keyId",
        "issuer", "issuerWorkloadIdentity", "audience", "controlPlaneEpoch",
        "signedAt", "payloadDigest"}
    obj = {k: v for k, v in rep.items() if k != "signature"}
    canon = canonical_payload(obj, PROFILE)
    assert "sha256:" + hashlib.sha256(canon).hexdigest() == sig["payloadDigest"]


def test_contract_id_deterministic_across_generatedAt():
    """contractId 锚定核心事实（不含 generatedAt）：两次构造一致；
    payloadDigest 随 generatedAt 变化（诚实：时间戳属于被签名事实）。"""
    import time

    r1 = build_report()
    time.sleep(1.1)  # generatedAt 为秒级 UTC
    r2 = build_report()
    assert r1["contractId"] == r2["contractId"]
    assert r1["signature"]["payloadDigest"] != r2["signature"]["payloadDigest"]
    # 结构等价（除时间戳/签名）
    a = {k: v for k, v in r1.items() if k not in ("generatedAt", "signature")}
    b = {k: v for k, v in r2.items() if k not in ("generatedAt", "signature")}
    assert a == b


def test_cached_report_idempotent():
    """进程内缓存：同一对象同一签名（generatedAt 固定）。"""
    r1, r2 = build_cached_report(), build_cached_report()
    assert r1 == r2
    assert r1["signature"]["payloadDigest"] == r2["signature"]["payloadDigest"]


def test_core_facts_roundtrip_matches_report():
    facts = core_facts()
    rep = build_report()
    for key in ("contractVersion", "workloadIdentity", "runtimeType",
                "runtimeVersion", "model", "resourceDefaults", "isolation"):
        assert rep[key] == facts[key]


def test_api_runtime_capabilities_endpoint():
    app = create_app(enable_worker=False)
    with TestClient(app) as c:
        resp = c.get("/api/v1/runtime/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["contractVersion"] == "2"
    assert body["runtimeType"] == "pi-single-node"
    assert "sha256:" in body["signature"]["payloadDigest"]