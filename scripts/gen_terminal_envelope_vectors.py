# -*- coding: utf-8 -*-
"""生成 attempt_terminal_envelope v2 契约向量到
contracts/test-vectors/attempt_terminal_envelope/v2/。

覆盖：成功（SUCCESS_COMPLETE，产物清单）/失败平台收敛（FAILURE_PLATFORM_PROOF
含 missingEvidenceReasons）/取消（CANCELLED_CONFIRMED）/pos-signed（确定性
seed 密钥真实 Ed25519 签名）；负例：outcomeClass 枚举外、resultArtifacts 重复
path、缺必填、digest 格式错、未知字段。

- terminalEnvelopeId = sha256(JCS({taskId, attemptId, runId, stepIndex,
  outcomeClass, status, stopReason, artifacts(sorted)}))[:32]——除 ID/self-digest/
  signature 外的完整不可变快照前缀；
- payloadDigest 由 canonical 重算后回填，再经 build_signature_envelope 完成
  §9.4 信封（self-digest 一致性由 codec 校验）；
- PI_VEC_OUT 覆盖输出目录（写 attempt_terminal_envelope/v2 子目录）；
  PI_TERMINAL_PRINT_FP=1 时打印测试密钥公钥指纹（登记 registry 用）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator  # noqa: E402

from app.contracts.codec import (  # noqa: E402
    SIGNATURE_ENVELOPE_KEYS,
    build_signature_envelope,
    canonical_payload,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
)
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "attempt_terminal_envelope" / "v2"
OUT = (Path(os.environ["PI_VEC_OUT"]) / "attempt_terminal_envelope" / "v2"
       if os.environ.get("PI_VEC_OUT") else OUT_DEFAULT)

SCHEMA = load_schema("attempt_terminal_envelope", "2")
PROFILE = load_digest_profile("attempt_terminal_envelope", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

TEST_KEY_SEED = bytes.fromhex(
    "c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c1c2")

TASK_ID = "0123456789abcdef"
ATTEMPT_ID = "1010101010101010"
RUN_ID = "2020202020202020"

_ART_A = {"path": "out/report.md", "digest": "sha256:" + "a" * 64, "size": 42, "kind": "file"}
_ART_B = {"path": "out/data.json", "digest": "sha256:" + "b" * 64, "size": 128, "kind": "file"}
_ART_C = {"path": "out/SUMMARY.md", "digest": "sha256:" + "c" * 64, "size": 7, "kind": "file"}
_OBS = {
    "platform": "single-node-local", "reportedBy": "pi.worker",
    "missingEvidenceReasons": [],
}


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(TEST_KEY_SEED)


def _sorted_artifacts(artifacts: list[dict]) -> list[dict]:
    return sorted(artifacts, key=lambda a: a["path"])


def _env_id(task_id: str, attempt_id: str, run_id: str, step_index: int,
            outcome_class: str, status: str, stop_reason: str | None,
            artifacts: list[dict], missing: list[str] | None = None,
            side_effects: list[str] | None = None) -> str:
    """ID 派生：除 ID/self-digest/signature 外的完整不可变前像（评审 block-2：
    含 runtimeObserved/缺失证据/副作用，防不同证据事实撞同一主键）。"""
    blob = jcs({
        "taskId": task_id, "attemptId": attempt_id, "runId": run_id,
        "stepIndex": step_index, "outcomeClass": outcome_class, "status": status,
        "stopReason": stop_reason, "artifacts": _sorted_artifacts(artifacts),
        "observed": {"platform": "single-node-local", "reportedBy": "pi.worker",
                     "missingEvidenceReasons": sorted(list(missing or []))},
        "sideEffects": sorted(list(side_effects or [])),
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def _build(*, outcome_class: str, status: str, stop_reason: str | None = None,
           artifacts: list[dict] | None = None,
           missing: list[str] | None = None,
           platform: str = "single-node-local",
           compute_digest: bool = True) -> dict:
    arts = _sorted_artifacts(artifacts or [])
    env = {
        "contractVersion": "2",
        "workloadIdentity": "pi.node",
        "terminalEnvelopeId": _env_id(
            TASK_ID, ATTEMPT_ID, RUN_ID, 1, outcome_class, status,
            stop_reason, arts, missing=list(missing or []),
            side_effects=[]),
        "taskId": TASK_ID, "attemptId": ATTEMPT_ID, "runId": RUN_ID,
        "stepIndex": 1, "outcomeClass": outcome_class, "status": status,
        "stopReason": stop_reason,
        "runtimeObserved": {"platform": platform, "reportedBy": "pi.worker",
                            "missingEvidenceReasons": list(missing or [])},
        "resultArtifacts": arts,
        "unacknowledgedSideEffects": [],
        "payloadDigest": "sha256:" + "0" * 64,  # 占位，随后回填
    }
    if not compute_digest:
        # 负例：载荷可含结构性矛盾（如排序键重复使 canonical 无法计算），
        # 摘要字段仅占位，聚焦 Schema 层拒绝原因（沿 execution_plan 模式）。
        return env
    env["payloadDigest"] = payload_digest(env, PROFILE)
    return env


def _sign(env: dict) -> None:
    meta = {
        "objectType": "attempt_terminal_envelope", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-terminal-vector",
        "issuer": "terminal-test", "issuerWorkloadIdentity": "pi.node",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-05T08:01:00Z",
    }
    # signature 为契约必填（评审 block-1）：预置合法 placeholder 通过 Schema
    # 校验后，再由 build_signature_envelope 重算并覆盖真实签名。
    env["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                        "value": base64.b64encode(
                            _signing_key().sign(b"placeholder")).decode("ascii")}
    _env, sig_in, _ = build_signature_envelope(env, SCHEMA, PROFILE, meta)
    env["signature"] = {**_env, "value": base64.b64encode(
        _signing_key().sign(sig_in)).decode("ascii")}
    if os.environ.get("PI_TERMINAL_PRINT_FP"):
        pub = _signing_key().public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        print("SK_TERMINAL_VECTOR_FP=" + hashlib.sha256(pub).hexdigest())


def _self_checks() -> None:
    """生成器级自检：ID 派生覆盖不可变字段（防碰撞回归，沿 execution_plan 模式）。"""
    kw = dict(task_id=TASK_ID, attempt_id=ATTEMPT_ID, run_id=RUN_ID,
              step_index=1, outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
              stop_reason=None, artifacts=[_ART_A])
    assert _env_id(**kw) != _env_id(**dict(kw, step_index=2)), "stepIndex 变化必须变 ID"
    assert _env_id(**kw) != _env_id(**dict(kw, attempt_id="9999999999999999")), \
        "attemptId 变化必须变 ID"
    assert _env_id(**kw) != _env_id(**dict(
        kw, outcome_class="FAILURE_PLATFORM_PROOF", status="FAILED",
        stop_reason="step 1 failed")), "outcomeClass/status 变化必须变 ID"
    assert _env_id(**kw) != _env_id(**dict(kw, missing=["x"])), \
        "缺失证据变化必须变 ID"
    assert _env_id(**kw) != _env_id(**dict(kw, side_effects=["y"])), \
        "副作用变化必须变 ID"


CASES = [
    ("pos-success", True, "SUCCESS_COMPLETE：产物清单含两文件",
     lambda: _build(outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                    artifacts=[_ART_A, _ART_B]), None),
    ("pos-failed-platform-proof", True, "FAILURE_PLATFORM_PROOF：含缺失证据原因",
     lambda: _build(outcome_class="FAILURE_PLATFORM_PROOF", status="FAILED",
                    stop_reason="step 1 failed",
                    missing=["node-telemetry-unavailable"]), None),
    ("pos-cancelled", True, "CANCELLED_CONFIRMED：控制面取消收敛",
     lambda: _build(outcome_class="CANCELLED_CONFIRMED", status="CANCELLED",
                    stop_reason="cancelled"), None),
    ("pos-no-artifacts", True, "无产物（空清单）",
     lambda: _build(outcome_class="FAILURE_PLATFORM_PROOF", status="FAILED",
                    stop_reason="budget exhausted",
                    missing=["node-telemetry-unavailable"]), None),
    ("pos-signed", True, "含 §9.4 签名信封（确定性测试密钥真实签名；乱序传入验证集合归一化）",
     lambda: _build(outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                    artifacts=[_ART_B, _ART_C, _ART_A]), None),
    ("neg-outcome-enum", False, "outcomeClass 枚举外必须拒绝",
     lambda: _build(outcome_class="MAYBE", status="SUCCESS", compute_digest=False), "enum"),
    ("neg-dup-artifact-path", False, "resultArtifacts 全同元素重复必须拒绝（uniqueItems）",
     lambda: _build(outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                    artifacts=[_ART_A, _ART_A], compute_digest=False), "uniqueItems"),
    ("neg-missing-required", False, "缺 required 字段（status）必须拒绝",
     lambda: {k: v for k, v in _build(
         outcome_class="CANCELLED_CONFIRMED", status="CANCELLED").items()
         if k != "status"}, "required"),
    ("neg-bad-digest", False, "产物 digest 非 sha256:64hex 必须拒绝",
     lambda: _build(outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                    artifacts=[dict(_ART_A, digest="sha256:zz")],
                    compute_digest=False), "pattern"),
    ("neg-unknown-field", False, "未知字段必须拒绝",
     lambda: _build(outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                    compute_digest=False) | {"bogusField": "x"}, "unknown field"),
]


def _prefill_signature(env: dict) -> None:
    """负例预置 Schema 合法的 placeholder 签名（评审 should-fix：缺 signature
    必填会遮蔽目标错误，隔离负例聚焦各字段违规）。"""
    env["signature"] = {
        "objectType": "attempt_terminal_envelope", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-terminal-vector",
        "issuer": "terminal-test", "issuerWorkloadIdentity": "pi.node",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-05T08:01:00Z",
        "payloadDigest": "sha256:" + "0" * 64,
        "value": base64.b64encode(_signing_key().sign(b"placeholder")).decode("ascii"),
    }


def main() -> int:
    _self_checks()
    vectors = []
    for cid, valid, note, build_fn, invalid_reason in CASES:
        obj = build_fn()
        if valid:  # 正向量一律含 §9.4 签名（signature 为契约必填，评审 block-1）
            _sign(obj)
        else:
            _prefill_signature(obj)
        errors = sorted(VALIDATOR.iter_errors(obj), key=lambda e: str(e.path))
        schema_valid = not errors
        entry = {
            "id": cid, "kind": "positive" if valid else "negative",
            "expectedSchemaValid": valid, "note": note,
            "expectedError": invalid_reason, "object": obj,
            "calculateOnLoad": True,
        }
        if valid:
            entry["canonicalPayloadB64"] = base64.b64encode(
                canonical_payload(obj, PROFILE)).decode()
            entry["payloadDigest"] = obj["payloadDigest"]
            entry["signatureInputB64"] = None if "signature" not in obj else (
                base64.b64encode(build_signature_envelope(
                    obj, SCHEMA, PROFILE,
                    {k: obj["signature"][k] for k in SIGNATURE_ENVELOPE_KEYS
                     if k in obj["signature"]})[1]).decode())
        else:
            entry["invalidReason"] = invalid_reason
        if schema_valid != valid:
            print(f"[!!] {cid}: 期望 schemaValid={valid} 实际={schema_valid} "
                  f"{[e.message for e in errors][:2]}")
        vectors.append(entry)

    pos_ids = [v["object"]["terminalEnvelopeId"] for v in vectors
               if v["kind"] == "positive"]
    assert len(pos_ids) == len(set(pos_ids)), \
        "正向量 terminalEnvelopeId 必须两两唯一（不可变字段全覆盖）"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "attempt_terminal_envelope", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())