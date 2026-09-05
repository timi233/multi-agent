# -*- coding: utf-8 -*-
"""ExecutionPlanSnapshot 契约语义校验（蓝图 §10.5.1 单机子集）。

verified_execution_plan(plan) -> list[str]：先 Schema（形状）再语义：
- canonicalPlannedInputsDigest 必须等于 sha256(JCS(plannedAttemptInputs
  按 plannedAttemptInputId 归一化排序))（集合乱序等价、内容变化必变）；
- 顶层 payloadDigest 必须等于 canonicalPayload 重算值（self-digest 兜底，
  与 codec build 校验一致）；
- planKind 仅 INITIAL（Schema enum 收紧）：parentExecutionPlanSnapshotId
  必须为 null（显式 null ≠ 绕过校验）；REPAIR 为蓝图保留名，契约字段
  未落地前不签发；
- plannedAttemptInputId 全局唯一（同 ID 不同内容拒收，不依赖 uniqueItems）；
- plannedAttemptInputs[].upstreamBindings[].producerNodeId 必须指向同一计划
  内已存在 workflowNodeId（禁止悬垂/伪造上游引用）。
digest 重算全程异常（ContractError）收敛为问题列表，不向外逃逸。
返回问题列表；空 = 合法快照。编译侧（orchestrator）只允许合法快照进入执行。
"""
from __future__ import annotations

import hashlib

from app.contracts.codec import (
    ContractError,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
    validate,
)


def _inputs_digest(plan: dict) -> str:
    inputs = sorted(
        plan.get("plannedAttemptInputs") or [],
        key=lambda i: i["plannedAttemptInputId"])
    return "sha256:" + hashlib.sha256(jcs(inputs)).hexdigest()


def _semantic_checks(plan: dict) -> list[str]:
    problems: list[str] = []

    if plan.get("planKind") == "INITIAL" and \
            plan.get("parentExecutionPlanSnapshotId") is not None:
        problems.append(
            f"planKind=INITIAL 但 parentExecutionPlanSnapshotId 非 null: "
            f"{plan.get('parentExecutionPlanSnapshotId')}")

    inputs = plan.get("plannedAttemptInputs") or []
    ids = [i.get("plannedAttemptInputId") for i in inputs]
    if len(ids) != len(set(ids)):
        problems.append(
            "plannedAttemptInputs 存在重复 plannedAttemptInputId（同 ID 不同内容）")

    try:
        expected = _inputs_digest(plan)
    except ContractError as exc:
        problems.append(f"canonicalPlannedInputsDigest 无法重算: {exc}")
        expected = None
    if expected is not None and plan.get("canonicalPlannedInputsDigest") != expected:
        problems.append(
            f"canonicalPlannedInputsDigest 与重算不一致: "
            f"object={plan.get('canonicalPlannedInputsDigest')} "
            f"recomputed={expected}")

    try:
        recomputed = payload_digest(plan, load_digest_profile(
            "execution_plan_snapshot", "2"))
    except ContractError as exc:
        problems.append(f"payloadDigest 无法重算: {exc}")
        recomputed = None
    if recomputed is not None and plan.get("payloadDigest") != recomputed:
        problems.append(
            f"payloadDigest self 不一致: object={plan.get('payloadDigest')} "
            f"recomputed={recomputed}")

    node_ids = {i["workflowNodeId"] for i in inputs}
    for i, inp in enumerate(inputs, start=1):
        for up in inp.get("upstreamBindings") or []:
            producer = up.get("producerNodeId")
            if producer not in node_ids:
                problems.append(
                    f"input[{i}] {inp.get('plannedAttemptInputId')} 上游引用 "
                    f"悬垂: producerNodeId={producer!r} 不在计划节点集合")
    return problems


def verified_execution_plan(plan: dict) -> list[str]:
    """完整校验 ExecutionPlanSnapshot：先 Schema（形状），再语义。"""
    schema_problems = validate(
        plan, load_schema("execution_plan_snapshot", "2"))
    if schema_problems:
        return schema_problems  # 形状无效时语义校验会误判，先报形状
    return _semantic_checks(plan)