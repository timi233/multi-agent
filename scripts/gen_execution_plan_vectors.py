# -*- coding: utf-8 -*-
"""生成 execution_plan_snapshot v2 契约向量到
contracts/test-vectors/execution_plan_snapshot/v2/。

覆盖：单步/多步（IMPLEMENTATION+READ_ONLY+IMPLEMENTATION）/README_ONLY/
pos-signed（确定性 seed 密钥真实 Ed25519 签名）；负例：重复 input id、
runKind 枚举外、缺必填、canonicalPlannedInputsDigest 格式错、未知字段。

- executionPlanSnapshotId = sha256(JCS({taskId, planKind, inputs(排序),
  taskSpecDigest, parentExecutionPlanSnapshotId, compilerId, compilerVersion,
  compiledBy, compiledAt}))[:32]  —— 除 ID/self-digest/signature 外的完整
  不可变快照前缀，任一不可变字段变化必变 ID（deterministic、compiledAt
  为脚本内固定常量保证可复现）；
- canonicalPlannedInputsDigest = sha256(JCS(排序后 plannedAttemptInputs))；
- payloadDigest 由 canonical 重算后回填，再经 build_signature_envelope 完成
  §9.4 信封（self-digest 一致性由 codec 校验）；
- PI_VEC_OUT 覆盖输出目录（写 execution_plan_snapshot/v2 子目录）；
  PI_EXECPLAN_PRINT_FP=1 时打印测试密钥公钥指纹（登记 registry 用）。
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

OUT_DEFAULT = ROOT / "contracts" / "test-vectors" / "execution_plan_snapshot" / "v2"
OUT = (Path(os.environ["PI_VEC_OUT"]) / "execution_plan_snapshot" / "v2"
       if os.environ.get("PI_VEC_OUT")
       else OUT_DEFAULT)

SCHEMA = load_schema("execution_plan_snapshot", "2")
PROFILE = load_digest_profile("execution_plan_snapshot", "2")
VALIDATOR = Draft202012Validator(SCHEMA)

# 固定 seed 确定性派生测试密钥（与 budget 向量区分；对应 keys.lock.json
# sk-orchestrator-vector）。非生产。
TEST_KEY_SEED = bytes.fromhex("a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1")

COMPILED_AT = "2026-09-05T08:00:00Z"
TASK_ID = "0123456789abcdef"

_STEP_1 = {
    "plannedAttemptInputId": "1010101010101010",
    "workflowNodeId": "step-1",
    "runKind": "IMPLEMENTATION",
    "deliverableKind": "CODE_CHANGE",
    "promptContent": "实现：在 work.md 写入标题与三行要点，并用 run_command 验证文件存在",
}
_STEP_2 = {
    "plannedAttemptInputId": "2020202020202020",
    "workflowNodeId": "step-2",
    "runKind": "READ_ONLY",
    "deliverableKind": "READ_ONLY_EVIDENCE",
    "promptContent": "只读验收：读 work.md 确认标题与要点齐全，给出 ok=true 的结论",
    "upstreamBindings": [
        {"slotId": "step1-output", "producerNodeId": "step-1", "required": True},
    ],
}
_STEP_3 = {
    "plannedAttemptInputId": "3030303030303030",
    "workflowNodeId": "step-3",
    "runKind": "IMPLEMENTATION",
    "deliverableKind": "CODE_CHANGE",
    "promptContent": "补充：追加一行版本号并运行 python3 -m py_compile 自检",
    "upstreamBindings": [
        {"slotId": "step2-verdict", "producerNodeId": "step-2", "required": True},
    ],
}


def _signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(TEST_KEY_SEED)


def _sorted_inputs(inputs: list[dict]) -> list[dict]:
    return sorted(inputs, key=lambda i: i["plannedAttemptInputId"])


def _inputs_digest(inputs: list[dict]) -> str:
    return "sha256:" + hashlib.sha256(
        jcs(_sorted_inputs(inputs))).hexdigest()


def _plan_id(task_id: str, plan_kind: str, inputs: list[dict],
             parent_id: str | None = None,
             task_spec_digest: str | None = None,
             compiler_version: str = "0.1.0",
             compiled_by: str = "pi-orchestrator@0.1.0",
             compiled_at: str = COMPILED_AT) -> str:
    """确定性 ID：除 executionPlanSnapshotId/payloadDigest/signature 外
    的完整不可变快照前缀（评审 block-1：ID 必须覆盖全部不可变字段——
    taskSpecDigest/父计划/编译器/编译时间任一变化都会碰撞）。"""
    blob = jcs({
        "taskId": task_id,
        "planKind": plan_kind,
        "inputs": _sorted_inputs(inputs),
        "parentExecutionPlanSnapshotId": parent_id,
        "taskSpecDigest": task_spec_digest,
        "compilerId": "pi-orchestrator",
        "compilerVersion": compiler_version,
        "compiledBy": compiled_by,
        "compiledAt": compiled_at,
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def _build(task_id: str, inputs: list[dict],
           plan_kind: str = "INITIAL",
           parent_id: str | None = None,
           task_spec_digest: str | None = None,
           compute_digest: bool = True) -> dict:
    plan = {
        "contractVersion": "2",
        "workloadIdentity": "pi.orchestrator",
        "executionPlanSnapshotId": _plan_id(task_id, plan_kind, inputs,
                                            parent_id, task_spec_digest),
        "taskId": task_id,
        "compilerId": "pi-orchestrator",
        "compilerVersion": "0.1.0",
        "planKind": plan_kind,
        "plannedAttemptInputs": inputs,
        "canonicalPlannedInputsDigest": _inputs_digest(inputs),
        "parentExecutionPlanSnapshotId": parent_id,
        "compiledBy": "pi-orchestrator@0.1.0",
        "compiledAt": COMPILED_AT,
        "payloadDigest": "sha256:" + "0" * 64,  # 占位：随后回填重算值
    }
    if task_spec_digest:
        plan["taskSpecDigest"] = task_spec_digest
    if not compute_digest:
        # 负例：载荷可含结构性矛盾（如重复 input id 使 canonical 无法计算），
        # 摘要字段仅占位，聚焦 Schema 层拒绝原因。
        plan["canonicalPlannedInputsDigest"] = "sha256:" + "0" * 64
        return plan
    digest = payload_digest(plan, PROFILE)
    plan["payloadDigest"] = digest
    return plan


def _sign(plan: dict) -> None:
    """§9.4 信封：keyId=sk-orchestrator-vector/issuer=orchestrator-test；
    value = seed 派生密钥对 signatureInput 的真实签名。"""
    meta = {
        "objectType": "execution_plan_snapshot", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": "sk-orchestrator-vector",
        "issuer": "orchestrator-test", "issuerWorkloadIdentity": "pi.orchestrator",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": "2026-09-05T08:01:00Z",
    }
    _env, sig_in, _ = build_signature_envelope(plan, SCHEMA, PROFILE, meta)
    plan["signature"] = {**_env, "value": base64.b64encode(
        _signing_key().sign(sig_in)).decode("ascii")}
    if os.environ.get("PI_EXECPLAN_PRINT_FP"):
        pub = _signing_key().public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        print("SK_ORCHESTRATOR_VECTOR_FP=" + hashlib.sha256(pub).hexdigest())


def _self_checks() -> None:
    """生成器级自检：ID 派生覆盖不可变字段（评审 block-1 防碰撞回归）。"""
    spec = "sha256:" + "b" * 64
    assert _plan_id(TASK_ID, "INITIAL", [_STEP_1]) != _plan_id(
        TASK_ID, "INITIAL", [_STEP_1], task_spec_digest=spec), \
        "taskSpecDigest 变化必须改变 executionPlanSnapshotId"
    assert _plan_id(TASK_ID, "INITIAL", [_STEP_1]) != _plan_id(
        TASK_ID, "INITIAL", [_STEP_1, _single_step2()]), \
        "inputs 变化必须改变 executionPlanSnapshotId"
    assert _plan_id(TASK_ID, "INITIAL", [_STEP_1]) != _plan_id(
        TASK_ID, "INITIAL", [_STEP_1], parent_id="a" * 32), \
        "父计划变化必须改变 executionPlanSnapshotId"
    assert _plan_id(TASK_ID, "INITIAL", [_STEP_1]) != _plan_id(
        TASK_ID, "INITIAL", [_STEP_1], compiled_at="2026-09-05T08:02:00Z"), \
        "编译时间变化必须改变 executionPlanSnapshotId"


def _single_step2() -> dict:
    """pos-readonly-step 用：单 READ_ONLY 步骤，无上游引用（避免悬垂）。"""
    step = dict(_STEP_2)
    step.pop("upstreamBindings", None)
    return step


CASES = [
    ("pos-single-step", True, "单步 IMPLEMENTATION 计划（最简）",
     lambda: _build(TASK_ID, [_STEP_1]), None),
    ("pos-multi-step", True, "三步计划：IMPLEMENTATION→READ_ONLY 验收→IMPLEMENTATION，含 upstreamBindings",
     lambda: _build(TASK_ID, [_STEP_1, _STEP_2, _STEP_3]), None),
    ("pos-readonly-step", True, "单步 READ_ONLY（只读证据，无上游引用）",
     lambda: _build(TASK_ID, [_single_step2()]), None),
    ("pos-with-spec-parent-null", True, "含 taskSpecDigest、parent=null（INITIAL）",
     lambda: _build(TASK_ID, [_STEP_1], task_spec_digest="sha256:" + "b" * 64),
     None),
    ("pos-signed", True, "含 §9.4 签名信封（确定性测试密钥真实签名）",
     lambda: _build(TASK_ID, [_STEP_1, _single_step2()]), None),
    ("neg-dup-input-id", False, "plannedAttemptInputId 重复必须拒绝",
     lambda: _build(TASK_ID, [_STEP_1, dict(_STEP_1)], compute_digest=False), "uniqueItems"),
    ("neg-run-kind-enum", False, "runKind 枚举外必须拒绝",
     lambda: _build(TASK_ID, [dict(_STEP_1, runKind="SCRIPT")], compute_digest=False), "enum"),
    ("neg-missing-required", False, "plannedAttemptInput 缺 deliverableKind 必须拒绝",
     lambda: _build(TASK_ID, [{k: v for k, v in _STEP_1.items()
                               if k != "deliverableKind"}], compute_digest=False), "required"),
    ("neg-bad-inputs-digest", False, "canonicalPlannedInputsDigest 非 sha256:64hex 必须拒绝",
     lambda: _build(TASK_ID, [_STEP_1]) | {"canonicalPlannedInputsDigest": "sha256:zz"},
     "pattern"),
    ("neg-unknown-field", False, "未知字段必须拒绝",
     lambda: _build(TASK_ID, [_STEP_1], compute_digest=False) | {"bogusField": "x"}, "unknown field"),
]


def main() -> int:
    _self_checks()
    vectors = []
    for cid, valid, note, build_fn, invalid_reason in CASES:
        obj = build_fn()
        if cid == "pos-signed":
            _sign(obj)
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

    pos_ids = [v["object"]["executionPlanSnapshotId"] for v in vectors
               if v["kind"] == "positive"]
    assert len(pos_ids) == len(set(pos_ids)), \
        "正向量 executionPlanSnapshotId 必须两两唯一（不可变字段全覆盖）"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "vectors.json").write_text(
        json.dumps({"objectType": "execution_plan_snapshot", "schemaVersion": "2",
                    "canonicalEncoding": "RFC8785-JCS-lite", "vectors": vectors},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"写 {len(vectors)} 个向量 -> {OUT / 'vectors.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())