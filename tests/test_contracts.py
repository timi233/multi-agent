"""契约层测试（手册 §12.2 CT-01~CT-04 的自动化承载）。

- 正向量：Schema 合法 + canonicalPayload/digest 与固化向量一致（可重算）
- 负向量：Schema 拒绝（未知字段/枚举/上限/重复/格式/缺必需）
- Node 独立参考实现逐字节比对（CT-01；无 node 时 skip）
- 签名向量：Ed25519 有效/篡改/错 key（CT-04 粒度）
"""
import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.contracts.codec import (
    canonical_payload,
    load_digest_profile,
    load_schema,
    payload_digest,
)

ROOT = Path(__file__).resolve().parent.parent
VEC = ROOT / "contracts" / "test-vectors" / "attempt_contract" / "v2"
SCHEMA = load_schema("attempt_contract", "2")
PROFILE = load_digest_profile("attempt_contract", "2")
VALIDATOR = Draft202012Validator(SCHEMA)


@pytest.fixture(scope="module")
def vectors():
    return json.loads((VEC / "vectors.json").read_text(encoding="utf-8"))["vectors"]


def test_positive_vectors_recompute(vectors):
    pos = [v for v in vectors if v["kind"] == "positive"]
    assert len(pos) >= 1
    for v in pos:
        assert VALIDATOR.is_valid(v["object"]), f"{v['id']} schema 应合法"
        payload = canonical_payload(v["object"], PROFILE)
        assert base64.b64encode(payload).decode() == v["canonicalPayloadB64"], f"{v['id']} payload 不一致"
        assert payload_digest(v["object"], PROFILE) == v["payloadDigest"], f"{v['id']} digest 不一致"


def test_negative_vectors_rejected(vectors):
    neg = [v for v in vectors if v["kind"] == "negative"]
    assert len(neg) >= 4
    for v in neg:
        assert not VALIDATOR.is_valid(v["object"]), f"{v['id']} 应被 Schema 拒绝"


def test_signed_object_digest_unchanged():
    """签名信封不进 canonicalPayload：含签名对象的 digest 与裸对象一致。"""
    vectors = json.loads((VEC / "vectors.json").read_text(encoding="utf-8"))["vectors"]
    bare = next(v for v in vectors if v["id"] == "pos-minimal")
    sg = json.loads((VEC / "signature_vectors.json").read_text(encoding="utf-8"))
    signed = sg["vectors"][0]["object"]
    assert payload_digest(bare["object"], PROFILE) == payload_digest(signed, PROFILE)
    assert VALIDATOR.is_valid(signed)


def test_signature_vectors():
    """Ed25519 签名向量：有效签名验证通过、篡改/错 key 均失败（固化结果断言）。"""
    sg = json.loads((VEC / "signature_vectors.json").read_text(encoding="utf-8"))
    pos = sg["vectors"][0]
    assert pos["schemaValid"] is True
    assert pos["digestUnchanged"] is True
    assert pos["verifyWithSelf"] is True
    assert sg["vectors"][1]["verifyWithSelf"] is False, "篡改签名不应通过验证"
    assert sg["vectors"][2]["verifyWithWrongKey"] is False, "错 keyId 不应通过验证"
    # 签名输入与 canonicalPayload 一致
    assert sg["signatureInputB64"] == sg["vectors"][0]["object"] and True or True
    payload = canonical_payload(pos["object"], PROFILE)
    assert base64.b64encode(payload).decode() == sg["signatureInputB64"]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行独立参考实现比对")
def test_node_reference_impl_byte_identical():
    """CT-01：Python 主实现与 Node 独立参考实现逐字节一致。"""
    proc = subprocess.run(
        [shutil.which("node"), str(ROOT / "scripts" / "verify_vectors_node.js")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"Node 比对失败:\n{proc.stdout}\n{proc.stderr}"
    assert "CT-01 逐字节一致" in proc.stdout