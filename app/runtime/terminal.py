# -*- coding: utf-8 -*-
"""AttemptTerminalEnvelope 契约语义校验与签发（蓝图 §11.1 单机子集）。

verified_terminal_envelope(env) -> list[str]：先 Schema（形状）再语义：
- resultArtifacts 按 path 键全局唯一（同 path 不同内容拒收，不依赖
  uniqueItems）；
- 顶层 payloadDigest 必须等于 canonicalPayload 重算值（self-digest 兜底）；
- outcomeClass/status 搭配一致性（SUCCESS_COMPLETE↔SUCCESS、
  FAILURE_*↔FAILED|BUDGET_EXHAUSTED、CANCELLED_CONFIRMED↔CANCELLED；
  DELIVERY_RECONCILING 仅保留名不达）；
- runtimeObserved.missingEvidenceReasons 非空时不得宣称 SUCCESS_COMPLETE
  （缺失证据即非完整成功，蓝图 §11.1：profile 不升级为成功）。
digest 重算全程异常（ContractError）收敛为问题列表，不向外逃逸。
返回问题列表；空 = 合法快照。worker 只归档合法信封（含真实 CAS 摘要）。

build_terminal_envelope(...)：worker 运行时签发（节点密钥 issuer=pi.node，
ID 派生规则与向量生成器一致：除 ID/self-digest/signature 外的完整不可变
前像）。
"""
from __future__ import annotations

import datetime
import hashlib

from app.contracts.codec import (
    ContractError,
    build_signature_envelope,
    jcs,
    load_digest_profile,
    load_schema,
    payload_digest,
    validate,
)
from app.security import keys as node_keys

# outcomeClass → 允许的 status 映射（梦行为规范）
_STATUS_BY_OUTCOME = {
    "SUCCESS_COMPLETE": {"SUCCESS"},
    "FAILURE_NODE_REPORTED": {"FAILED", "BUDGET_EXHAUSTED"},
    "FAILURE_PLATFORM_PROOF": {"FAILED", "BUDGET_EXHAUSTED"},
    "CANCELLED_CONFIRMED": {"CANCELLED"},
    "DELIVERY_RECONCILING": {"FAILED"},  # 保留名（本版不达，仅结构允许）
}


def _semantic_checks(env: dict) -> list[str]:
    problems: list[str] = []

    artifacts = env.get("resultArtifacts") or []
    paths = [a.get("path") for a in artifacts]
    if len(paths) != len(set(paths)):
        problems.append("resultArtifacts 存在重复 path（同 path 不同内容）")

    try:
        recomputed = payload_digest(env, load_digest_profile(
            "attempt_terminal_envelope", "2"))
    except ContractError as exc:
        problems.append(f"payloadDigest 无法重算: {exc}")
        recomputed = None
    if recomputed is not None and env.get("payloadDigest") != recomputed:
        problems.append(
            f"payloadDigest self 不一致: object={env.get('payloadDigest')} "
            f"recomputed={recomputed}")

    outcome = env.get("outcomeClass")
    status = env.get("status")
    allowed = _STATUS_BY_OUTCOME.get(outcome)
    if allowed is not None and status not in allowed:
        problems.append(f"outcomeClass={outcome} 与 status={status} 搭配不一致")

    missing = env.get("runtimeObserved", {}).get("missingEvidenceReasons") or []
    if outcome == "SUCCESS_COMPLETE" and missing:
        problems.append(
            "SUCCESS_COMPLETE 不得有缺失证据原因（缺失证据不能升级为成功，§11.1）")
    return problems


def verified_terminal_envelope(env: dict) -> list[str]:
    """完整校验 AttemptTerminalEnvelope：先 Schema（形状），再语义。"""
    schema_problems = validate(env, load_schema("attempt_terminal_envelope", "2"))
    if schema_problems:
        return schema_problems  # 形状无效时语义校验会误判，先报形状
    return _semantic_checks(env)


def build_terminal_envelope(*, task_id: str, attempt_id: str, run_id: str,
                            step_index: int, outcome_class: str, status: str,
                            stop_reason: str | None, result_artifacts: list[dict],
                            missing_evidence: list[str] | None = None,
                            side_effects: list[str] | None = None) -> dict:
    """运行时签发 AttemptTerminalEnvelope（节点密钥，issuer=pi.node）。

    产物调用方（evidence 收存）负责把 result_artifacts 的 digest 实际写入
    CAS；信封 ID 派生除 ID/self-digest/signature 外的**完整不可变前像**
    （评审 block-2：含 runtimeObserved/missingEvidenceReasons/sideEffects），
    与 scripts/gen_terminal_envelope_vectors.py 规则一致。
    """
    def _env_id() -> str:
        blob = jcs({
            "taskId": task_id, "attemptId": attempt_id, "runId": run_id,
            "stepIndex": step_index, "outcomeClass": outcome_class,
            "status": status, "stopReason": stop_reason,
            "artifacts": sorted(result_artifacts, key=lambda a: a["path"]),
            "observed": {"platform": "single-node-local",
                         "reportedBy": "pi.worker",
                         "missingEvidenceReasons": sorted(missing_evidence or [])},
            "sideEffects": sorted(side_effects or []),
        })
        return hashlib.sha256(blob).hexdigest()[:32]

    env = {
        "contractVersion": "2",
        "workloadIdentity": "pi.node",
        "terminalEnvelopeId": _env_id(),
        "taskId": task_id, "attemptId": attempt_id, "runId": run_id,
        "stepIndex": step_index, "outcomeClass": outcome_class, "status": status,
        "stopReason": stop_reason,
        "runtimeObserved": {
            "platform": "single-node-local", "reportedBy": "pi.worker",
            "missingEvidenceReasons": sorted(missing_evidence or []),
        },
        "resultArtifacts": sorted(result_artifacts, key=lambda a: a["path"]),
        "unacknowledgedSideEffects": sorted(side_effects or []),
        "payloadDigest": "sha256:" + "0" * 64,  # 占位，随后回填
    }
    env["payloadDigest"] = payload_digest(env, load_digest_profile(
        "attempt_terminal_envelope", "2"))
    now = datetime.datetime.now(datetime.timezone.utc)
    meta = {
        "objectType": "attempt_terminal_envelope", "schemaVersion": "2",
        "signatureAlgorithm": "Ed25519", "keyId": node_keys.key_id(),
        "issuer": "pi.node", "issuerWorkloadIdentity": "pi.node",
        "audience": "pi.platform", "controlPlaneEpoch": 0,
        "signedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # signature 为契约必填：预置合法 placeholder 通过 Schema 校验后重签覆盖
    env["signature"] = {**meta, "payloadDigest": "sha256:" + "0" * 64,
                        "value": node_keys.sign(b"placeholder")}
    _env, sig_in, _ = build_signature_envelope(
        env, load_schema("attempt_terminal_envelope", "2"),
        load_digest_profile("attempt_terminal_envelope", "2"), meta)
    env["signature"] = {**_env, "value": node_keys.sign(sig_in)}
    return env


def verify_terminal_signature(env: dict) -> bool:
    """验签（评审 block-1/2）：信封元数据必须精确绑定当前节点身份
    （keyId/issuer/issuerWorkloadIdentity/audience/objectType/schemaVersion），
    signature.payloadDigest 必须等于对象重算值与信封声明值；重建 §9.4
    签名输入后用节点公钥 Ed25519 验签。任何不符返回 False。"""
    from app.contracts.codec import SIGNATURE_ENVELOPE_KEYS
    try:
        sig = env["signature"]
        if sig.get("objectType") != "attempt_terminal_envelope":
            return False
        if sig.get("schemaVersion") != "2":
            return False
        if sig.get("keyId") != node_keys.key_id():
            return False
        if sig.get("issuer") != "pi.node" or \
                sig.get("issuerWorkloadIdentity") != "pi.node":
            return False
        if sig.get("audience") != "pi.platform":
            return False
        recomputed = payload_digest(env, load_digest_profile(
            "attempt_terminal_envelope", "2"))
        if sig.get("payloadDigest") != recomputed or \
                env.get("payloadDigest") != recomputed:
            return False
        meta = {k: sig[k] for k in SIGNATURE_ENVELOPE_KEYS if k in sig}
        _env, sig_in, _ = build_signature_envelope(
            env, load_schema("attempt_terminal_envelope", "2"),
            load_digest_profile("attempt_terminal_envelope", "2"), meta)
        return node_keys.verify(sig_in, sig["value"])
    except Exception:
        return False