# -*- coding: utf-8 -*-
"""编排编译（蓝图 §6.3/§6.4 单机子集：compile_plan）。

Task 进入执行前编译为不可变、签名的 ExecutionPlanSnapshot（契约
execution_plan_snapshot v2）：旧任务（无 plan 字段）编译为默认单步
IMPLEMENTATION 计划（兼容回归，行为与 G1b 前一致）；携带 plan 步骤的
任务编译为多步计划，按 plannedAttemptInputs 次序执行（依赖由次序保证，
本版不做上游产物绑定）。

运行时签名信封：issuer=pi.orchestrator / keyId=节点密钥（RT 同源）。
正式 Orchestrator 独立密钥随 Phase 0 ADR / G4 拆分（README 披露）。
"""
from __future__ import annotations

import datetime
import hashlib
import uuid

from app.contracts.codec import (
    build_signature_envelope,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
)
from app.runtime.plans import verified_execution_plan
from app.security import keys as node_keys

COMPILER_ID = "pi-orchestrator"
COMPILER_VERSION = "0.1.0"
_ALLOWED_RUN_KINDS = ("IMPLEMENTATION", "REVIEW", "READ_ONLY")
_ALLOWED_DELIVERABLE_KINDS = ("CODE_CHANGE", "REVIEW_EVIDENCE", "READ_ONLY_EVIDENCE")


def _now_z() -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"))


def _sorted_inputs(inputs: list[dict]) -> list[dict]:
    return sorted(inputs, key=lambda i: i["plannedAttemptInputId"])


def _inputs_digest(inputs: list[dict]) -> str:
    return "sha256:" + hashlib.sha256(jcs(_sorted_inputs(inputs))).hexdigest()


def _plan_id(task_id: str, plan_kind: str, inputs: list[dict],
             compiled_by: str, compiled_at: str) -> str:
    """与向量生成器同规则的确定性 ID：除 ID/self-digest/signature 外的
    完整不可变快照前缀（taskSpecDigest 运行时无，固定 null）。"""
    blob = jcs({
        "taskId": task_id, "planKind": plan_kind, "inputs": _sorted_inputs(inputs),
        "parentExecutionPlanSnapshotId": None, "taskSpecDigest": None,
        "compilerId": COMPILER_ID, "compilerVersion": COMPILER_VERSION,
        "compiledBy": compiled_by, "compiledAt": compiled_at,
    })
    return hashlib.sha256(blob).hexdigest()[:32]


def compile_plan(task: dict) -> dict:
    """把任务编译为签名的 ExecutionPlanSnapshot（INITIAL）。

    - task["plan"]（JSONB 步骤数组，每项 {runKind, deliverableKind,
      promptContent, upstreamBindings?}）存在 → 编译为多步计划；
    - 否则默认单步 IMPLEMENTATION（promptContent=task["prompt"]）。
    构造后经 verified_execution_plan 自检，非法即抛 ValueError（开发期暴露）。
    """
    plan_raw = task.get("plan")
    if plan_raw is None:
        raw_steps = [{
            "runKind": "IMPLEMENTATION",
            "deliverableKind": "CODE_CHANGE",
            "promptContent": task["prompt"],
        }]
    elif isinstance(plan_raw, list) and plan_raw:
        raw_steps = plan_raw
    else:
        raise ValueError("plan 必须为非空步骤数组")

    inputs: list[dict] = []
    for i, s in enumerate(raw_steps, start=1):
        if s.get("runKind") not in _ALLOWED_RUN_KINDS:
            raise ValueError(f"step {i}: 非法 runKind={s.get('runKind')!r}")
        if s.get("deliverableKind") not in _ALLOWED_DELIVERABLE_KINDS:
            raise ValueError(
                f"step {i}: 非法 deliverableKind={s.get('deliverableKind')!r}")
        if not isinstance(s.get("promptContent"), str) or not s["promptContent"]:
            raise ValueError(f"step {i}: 缺少非空 promptContent")
        inp: dict = {
            "plannedAttemptInputId": uuid.uuid4().hex[:16],
            "workflowNodeId": f"step-{i}",
            "runKind": s["runKind"],
            "deliverableKind": s["deliverableKind"],
            "promptContent": s["promptContent"],
        }
        if s.get("upstreamBindings"):
            inp["upstreamBindings"] = s["upstreamBindings"]
        inputs.append(inp)

    compiled_by = f"{COMPILER_ID}@{COMPILER_VERSION}"
    compiled_at = _now_z()
    plan = {
        "contractVersion": "2",
        "workloadIdentity": "pi.orchestrator",
        "executionPlanSnapshotId": _plan_id(
            task["id"], "INITIAL", inputs, compiled_by, compiled_at),
        "taskId": task["id"],
        "compilerId": COMPILER_ID,
        "compilerVersion": COMPILER_VERSION,
        "planKind": "INITIAL",
        "plannedAttemptInputs": inputs,
        "canonicalPlannedInputsDigest": _inputs_digest(inputs),
        "parentExecutionPlanSnapshotId": None,
        "compiledBy": compiled_by,
        "compiledAt": compiled_at,
        "payloadDigest": "sha256:" + "0" * 64,  # 占位，随后回填
    }
    digest = payload_digest(plan, load_digest_profile("execution_plan_snapshot", "2"))
    plan["payloadDigest"] = digest

    meta = {
        "objectType": "execution_plan_snapshot", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": node_keys.key_id(),
        "issuer": "pi.orchestrator", "issuerWorkloadIdentity": "pi.orchestrator",
        "audience": "pi.platform", "controlPlaneEpoch": 0, "signedAt": _now_z(),
    }
    env, sig_in, _ = build_signature_envelope(
        plan, load_schema("execution_plan_snapshot", "2"),
        load_digest_profile("execution_plan_snapshot", "2"), meta)
    plan["signature"] = {**env, "value": node_keys.sign(sig_in)}

    problems = verified_execution_plan(plan)
    if problems:
        raise ValueError("compile_plan 产物未通过契约语义校验: " + "; ".join(problems[:3]))
    return plan