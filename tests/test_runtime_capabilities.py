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
from app.security import keys as node_keys

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
    assert rep["isolation"]["networkEnabledForTools"] is True  # run_command 可触网，如实声明
    assert rep["knownGaps"] == RT_KNOWN_GAPS  # 事实基线如实披露


def test_schema_and_profile_consistency():
    from app.contracts.codec import validate_profile_consistency

    assert validate_profile_consistency(SCHEMA, PROFILE) == []
    errors = list(Draft202012Validator(SCHEMA).iter_errors(build_report()))
    assert errors == []


def test_signature_envelope_digest_recomputable():
    """信封 payloadDigest == 独立重算 canonicalPayload 的 sha256；
    value == 对 signature_input 的真实 Ed25519 签名（可验签）。"""
    from app.contracts.codec import build_signature_envelope as rebuild

    rep = build_report()
    sig = rep["signature"]
    assert {k for k in sig} == {
        "objectType", "schemaVersion", "signatureAlgorithm", "keyId",
        "issuer", "issuerWorkloadIdentity", "audience", "controlPlaneEpoch",
        "signedAt", "payloadDigest", "value"}
    obj = {k: v for k, v in rep.items() if k != "signature"}
    canon = canonical_payload(obj, PROFILE)
    assert "sha256:" + hashlib.sha256(canon).hexdigest() == sig["payloadDigest"]
    # 用 envelope 原 meta 重算 signature_input，验签 value（评审 block-fix）
    meta = {k: sig[k] for k in (
        "objectType", "schemaVersion", "signatureAlgorithm", "keyId",
        "issuer", "issuerWorkloadIdentity", "audience", "controlPlaneEpoch",
        "signedAt")}
    _, sig_input, _ = rebuild(obj, SCHEMA, PROFILE, meta)
    assert node_keys.verify(sig_input, sig["value"]) is True
    assert node_keys.verify(b"tampered-bytes", sig["value"]) is False


def test_contract_id_deterministic_across_generatedAt():
    """contractId 锚定核心事实（不含 generatedAt）：两次构造一致；
    事实变化（模型名/工具集合顺序）必然改变 contractId。"""
    import time

    from app.runtime.capabilities import _contract_id

    r1 = build_report()
    time.sleep(1.1)  # generatedAt 为秒级 UTC
    r2 = build_report()
    assert r1["contractId"] == r2["contractId"]
    assert r1["signature"]["payloadDigest"] != r2["signature"]["payloadDigest"]
    f1 = core_facts()
    f2 = dict(f1, model={"provider": "cliproxy-local", "name": "other-model"})
    assert _contract_id(f1) != _contract_id(f2)
    f3 = dict(f1, toolCapabilities=list(reversed(f1["toolCapabilities"])))
    assert _contract_id(f1) != _contract_id(f3)


def test_cached_report_idempotent_and_isolated():
    """缓存幂等 + deepcopy 隔离：外部修改返回对象不污染缓存（评审 should-fix）。"""
    r1 = build_cached_report()
    r2 = build_cached_report()
    assert r1 == r2
    assert r1["signature"]["payloadDigest"] == r2["signature"]["payloadDigest"]
    r1["toolCapabilities"][0]["name"] = "tampered"
    r1["model"]["name"] = "tampered"
    r3 = build_cached_report()
    assert r3["model"]["name"] != "tampered"
    assert "tampered" not in {t["name"] for t in r3["toolCapabilities"]}


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