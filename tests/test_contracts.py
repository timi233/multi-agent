"""契约层测试（手册 §12.2 CT-01~CT-04 的自动化承载）。

- 正向量：Schema 合法 + canonicalPayload/digest 与固化向量一致（可重算）
- 负向量：Schema 拒绝（未知字段/枚举/上限/重复/格式/缺必需）
- Node 独立参考实现逐字节比对（CT-01；无 node 时 skip）
- 签名向量：运行时 Ed25519 真实验签，正例通过、篡改任一信封字段失败、
  错 keyId（同一签名换公钥）失败 —— 域分离签名输入（CT-04）
- fail-closed：verified_payload_digest 拒绝含未知字段的对象
- 边界：整数超跨语言安全范围拒绝
"""
import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.contracts.codec import (
    SIGNATURE_ENVELOPE_KEYS,
    ContractError,
    build_signature_envelope,
    canonical_payload,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
    signature_input,
    verified_payload_digest,
)

ROOT = Path(__file__).resolve().parent.parent
VEC = ROOT / "contracts" / "test-vectors" / "attempt_contract" / "v2"
KEY_DIR = ROOT / "deploy" / "keys"
SCHEMA = load_schema("attempt_contract", "2")
PROFILE = load_digest_profile("attempt_contract", "2")
VALIDATOR = Draft202012Validator(SCHEMA)


@pytest.fixture(scope="module")
def vectors():
    return json.loads((VEC / "vectors.json").read_text(encoding="utf-8"))["vectors"]


def _openssl_verify(pubkey: Path, data: bytes, sig_hex: str) -> bool:
    with tempfile.NamedTemporaryFile() as df, tempfile.NamedTemporaryFile() as sf:
        df.write(data)
        df.flush()
        sf.write(bytes.fromhex(sig_hex))
        sf.flush()
        proc = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pubkey),
             "-rawin", "-in", df.name, "-sigfile", sf.name],
            capture_output=True, text=True,
        )
    return proc.returncode == 0 and "Verified" in proc.stdout


def test_positive_vectors_recompute(vectors):
    pos = [v for v in vectors if v["kind"] == "positive"]
    assert len(pos) >= 1
    for v in pos:
        assert VALIDATOR.is_valid(v["object"]), f"{v['id']} schema 应合法"
        payload = canonical_payload(v["object"], PROFILE)
        assert base64.b64encode(payload).decode() == v["canonicalPayloadB64"], f"{v['id']} payload 不一致"
        assert payload_digest(v["object"], PROFILE) == v["payloadDigest"], f"{v['id']} digest 不一致"


def test_negative_vectors_rejected(vectors):
    """负向量必须被当前 Schema 实时拒绝（评审 fix-3：不依赖固化布尔值）。"""
    neg = [v for v in vectors if v["kind"] == "negative"]
    assert len(neg) >= 4
    for v in neg:
        assert not VALIDATOR.is_valid(v["object"]), f"{v['id']} 应被 Schema 拒绝"
        assert v["expectedError"], f"{v['id']} 需声明可观察的错误关键字"


def test_signed_object_digest_unchanged():
    """签名信封不进 canonicalPayload：含签名对象的 digest 与裸对象一致。"""
    vectors = json.loads((VEC / "vectors.json").read_text(encoding="utf-8"))["vectors"]
    bare = next(v for v in vectors if v["id"] == "pos-minimal")
    sg = json.loads((VEC / "signature_vectors.json").read_text(encoding="utf-8"))
    signed = sg["vectors"][0]["object"]
    assert payload_digest(bare["object"], PROFILE) == payload_digest(signed, PROFILE)
    assert VALIDATOR.is_valid(signed)


def test_signature_vectors():
    """运行时验证签名向量：正例通过；篡改任一信封字段失败；错 key 失败。

    不使用固化布尔值——所有验签在本测试中经 openssl 真实重算。
    """
    sg = json.loads((VEC / "signature_vectors.json").read_text(encoding="utf-8"))
    pub = KEY_DIR / "sk-attempt.pub.pem"
    pub_other = KEY_DIR / "sk-node.pub.pem"

    pos = sg["vectors"][0]
    assert pos["schemaValid"] is True
    assert pos["digestUnchanged"] is True  # 签名信封不进 payload
    sig_input = base64.b64decode(sg["signatureInputB64"])
    assert _openssl_verify(pub, sig_input, pos["signatureHex"]), "有效签名必须验通"

    # 篡改任一信封字段（keyId/issuer/signedAt/audience/epoch/payloadDigest）都必须失败
    for v in sg["vectors"][1:]:
        if v["id"] == "sig-wrong-key":
            continue
        env = dict(sg["envelope"])
        env.update(v["envelopeMutated"])
        mutated_input = signature_input(None, env)
        assert not _openssl_verify(pub, mutated_input, pos["signatureHex"]), \
            f"{v['id']} 篡改后不得通过验证"

    # 错 keyId：用不匹配公钥验证同一个有效签名
    wrong = next(v for v in sg["vectors"] if v["id"] == "sig-wrong-key")
    assert not _openssl_verify(pub_other, sig_input, pos["signatureHex"])
    assert wrong["verifyWithWrongKey"] is False


def test_signature_rebuild_from_object():
    """从正例对象原子重建签名输入（评审 fix-5）：Schema 校验 → 重算 digest →
    §9.4 信封 → 签名输入；信封字段必须与对象签名信封自洽并验证通过。

    不信任固化的 signatureInputB64/布尔——digest 与输入全部实时重算。
    """
    sg = json.loads((VEC / "signature_vectors.json").read_text(encoding="utf-8"))
    sg_vec = json.loads((VEC / "vectors.json").read_text(encoding="utf-8"))
    pos = sg["vectors"][0]
    obj = pos["object"]
    pub = KEY_DIR / "sk-attempt.pub.pem"

    # 对象签名信封内的 payloadDigest 必须等于重算值（自洽）
    recomputed = payload_digest(obj, PROFILE)
    assert obj["signature"]["payloadDigest"] == recomputed

    # 从对象 signature 提取 meta（不含 value/payloadDigest），原子重建
    meta = {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS}
    env, rebuilt, digest = build_signature_envelope(obj, SCHEMA, PROFILE, meta)
    assert digest == recomputed
    assert env["payloadDigest"] == recomputed
    assert base64.b64encode(rebuilt).decode() == sg["signatureInputB64"], \
        "重建签名输入必须与生成器一致"
    assert _openssl_verify(pub, rebuilt, pos["signatureHex"]), \
        "重建输入上运行时的签名必须验通"

    # 篡改余下字段（signatureAlgorithm/issuerWorkloadIdentity/objectType/schemaVersion）
    # 也必须在同一签名上失败（覆盖 10 字段信封审计）
    tampered = dict(env)
    for key, val in [("signatureAlgorithm", "ECDSA"),
                     ("issuerWorkloadIdentity", "pi.evil"),
                     ("objectType", "other_contract"),
                     ("schemaVersion", "1")]:
        e = dict(tampered, **{key: val})
        assert not _openssl_verify(pub, signature_input(b"", e), pos["signatureHex"]), \
            f"篡改 {key} 后不得通过验证"


def test_verified_payload_digest_fail_closed():
    """原子路径：未知字段必须在 digest 前被 Schema 拒绝（评审 fix-3）。"""
    obj = json.loads((VEC / "vectors.json").read_text(encoding="utf-8"))
    base = next(v for v in obj["vectors"] if v["id"] == "pos-minimal")["object"]
    base = json.loads(json.dumps(base))

    with_unknown = dict(base, unexpectedField="must-be-rejected")
    with pytest.raises(ContractError, match="SCHEMA_INVALID"):
        verified_payload_digest(with_unknown, SCHEMA, PROFILE)

    # 合法对象正常通过
    d = verified_payload_digest(base, SCHEMA, PROFILE)
    assert d == payload_digest(base, PROFILE)


def test_integer_safe_range_enforced():
    """跨语言安全整数：超出 ±(2^53-1) 的整数拒绝（评审 fix-5）。"""
    big = 2**53
    with pytest.raises(ContractError, match="safe range"):
        jcs({"n": big})
    with pytest.raises(ContractError, match="safe range"):
        jcs({"n": -big})
    # 边界值本身合法
    assert jcs({"n": 2**53 - 1}) == b'{"n":9007199254740991}'


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 运行独立参考实现比对")
def test_node_reference_impl_byte_identical():
    """CT-01：Python 主实现与 Node 独立参考实现逐字节一致（含 unicode/代理对转义）。"""
    proc = subprocess.run(
        [shutil.which("node"), str(ROOT / "scripts" / "verify_vectors_node.js")],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"Node 比对失败:\n{proc.stdout}\n{proc.stderr}"
    assert "CT-01 逐字节一致" in proc.stdout