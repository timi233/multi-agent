"""向量生成器可复现性（评审 fix-11）+ 原子签名入口反例（fix-8/fix-9）。

- 在临时目录以 PI_VEC_OUT 重跑三个 gen_* 脚本，输出必须与固化的工作区向量
  逐字节一致（生成器幂等/可复现）。
- build_signature_envelope 必须拒绝错误对象域/版本 meta 与 self-digest 不一致对象。
- DigestProfile 的 optionalImmutablePointers 必须与 Schema 一致（存在且可选）。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.contracts.codec import (
    ContractError,
    build_signature_envelope,
    load_digest_profile,
    load_schema,
    validate_profile_consistency,
)

ROOT = Path(__file__).resolve().parent.parent
VEC = ROOT / "contracts" / "test-vectors"

GEN_SCRIPTS = ["gen_contract_vectors.py", "gen_signature_vectors.py",
               "gen_protocol_vectors.py"]
# gen_contract/gen_signature 的 OUT 就是 attempt_contract/v2 目录；
# gen_protocol 的 OUT 是 test-vectors 根（写 <obj>/v2/...）
INDEPENDENT_SCRIPTS = ["gen_contract_vectors.py", "gen_protocol_vectors.py"]


@pytest.mark.parametrize("script", INDEPENDENT_SCRIPTS)
def test_generator_runs_in_tmp(script):
    """独立生成器可在临时输出目录正常运行（冒烟；逐字节一致性见下测试）。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PI_VEC_OUT": tmp}
        proc = subprocess.run(
            [sys.executable, f"scripts/{script}"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, f"{script} 失败:\n{proc.stderr}"


def test_generators_output_via_tmp_identical():
    """临时目录按依赖序重跑全部生成器，输出与工作区固化向量逐字节一致（评审 fix-11）。"""
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "PI_VEC_OUT": tmp}
        for script in GEN_SCRIPTS:  # 依赖序：gen_signature 读 gen_contract 输出
            proc = subprocess.run(
                [sys.executable, f"scripts/{script}"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            assert proc.returncode == 0, f"{script} 失败:\n{proc.stderr}"

        pairs = [
            (Path(tmp) / "vectors.json",
             VEC / "attempt_contract" / "v2" / "vectors.json"),
            (Path(tmp) / "signature_vectors.json",
             VEC / "attempt_contract" / "v2" / "signature_vectors.json"),
            (Path(tmp) / "task_spec" / "v2" / "vectors.json",
             VEC / "task_spec" / "v2" / "vectors.json"),
            (Path(tmp) / "event_envelope" / "v2" / "vectors.json",
             VEC / "event_envelope" / "v2" / "vectors.json"),
        ]
        for fresh, tracked in pairs:
            assert fresh.read_bytes() == tracked.read_bytes(), \
                f"{tracked.name} 与生成器输出不一致（生成器不可复现）"


def test_build_envelope_rejects_wrong_domain():
    """原子入口拒绝错误对象域/版本 meta（评审 fix-8）。"""
    schema = load_schema("task_spec", "2")
    profile = load_digest_profile("task_spec", "2")
    obj = json.loads((VEC / "task_spec" / "v2" / "vectors.json").read_text(encoding="utf-8"))
    obj = next(v for v in obj["vectors"] if v["id"] == "pos-minimal")["object"]
    meta = {"objectType": "task_spec", "schemaVersion": "2",
            "signatureAlgorithm": "Ed25519", "keyId": "sk-attempt",
            "issuer": "attempt-service", "issuerWorkloadIdentity": "pi.x",
            "audience": None, "controlPlaneEpoch": 0, "signedAt": "2026-09-05T08:01:00Z"}
    with pytest.raises(ContractError, match="objectType mismatch"):
        build_signature_envelope(obj, schema, profile, {**meta, "objectType": "other"})
    with pytest.raises(ContractError, match="schemaVersion mismatch"):
        build_signature_envelope(obj, schema, profile, {**meta, "schemaVersion": "1"})


def test_build_envelope_rejects_self_digest_mismatch():
    """事件信封 self-digest 不一致必须被原子入口拒绝（评审 fix-8）。"""
    schema = load_schema("event_envelope", "2")
    profile = load_digest_profile("event_envelope", "2")
    data = json.loads((VEC / "event_envelope" / "v2" / "vectors.json").read_text(encoding="utf-8"))
    obj = next(v for v in data["vectors"] if v["id"] == "pos-task-event")["object"]
    meta = {"objectType": "event_envelope", "schemaVersion": "2",
            "signatureAlgorithm": "Ed25519", "keyId": "sk-attempt",
            "issuer": "evidence-service", "issuerWorkloadIdentity": "pi.evidence",
            "audience": None, "controlPlaneEpoch": 42, "signedAt": "2026-09-05T08:01:00Z"}
    # 自洽对象：应能构造（对象域匹配 + self-digest 等于重算值）
    env, sig_in, digest = build_signature_envelope(obj, schema, profile, meta)
    assert env["payloadDigest"] == obj["payloadDigest"]
    # 篡改对象内 payloadDigest（脱离重算值）→ 必须拒绝
    bad = json.loads(json.dumps(obj))
    bad["payloadDigest"] = "sha256:" + "0" * 64
    with pytest.raises(ContractError, match="self-digest mismatch"):
        build_signature_envelope(bad, schema, profile, meta)


def test_build_envelope_rejects_bad_field_values():
    """组装后的信封字段必须通过值域校验（评审 nit-1）。"""
    schema = load_schema("task_spec", "2")
    profile = load_digest_profile("task_spec", "2")
    data = json.loads((VEC / "task_spec" / "v2" / "vectors.json").read_text(encoding="utf-8"))
    obj = next(v for v in data["vectors"] if v["id"] == "pos-minimal")["object"]
    meta = {"objectType": "task_spec", "schemaVersion": "2",
            "signatureAlgorithm": "Ed25519", "keyId": "sk-attempt",
            "issuer": "attempt-service", "issuerWorkloadIdentity": "pi.x",
            "audience": None, "controlPlaneEpoch": 0, "signedAt": "2026-09-05T08:01:00Z"}
    with pytest.raises(ContractError):
        build_signature_envelope(obj, schema, profile, {**meta, "signatureAlgorithm": "ECDSA"})
    with pytest.raises(ContractError):
        build_signature_envelope(obj, schema, profile, {**meta, "controlPlaneEpoch": -1})
    with pytest.raises(ContractError):
        build_signature_envelope(obj, schema, profile, {**meta, "keyId": ""})
    with pytest.raises(ContractError):
        build_signature_envelope(obj, schema, profile, {**meta, "signedAt": "not-a-time"})


@pytest.mark.parametrize("object_type", ["attempt_contract", "task_spec",
                                         "event_envelope"])
def test_profile_consistency(object_type):
    """DigestProfile.optionalImmutablePointers 必须与 Schema 一致（评审 fix-9）。"""
    schema = load_schema(object_type, "2")
    profile = load_digest_profile(object_type, "2")
    assert validate_profile_consistency(schema, profile) == []