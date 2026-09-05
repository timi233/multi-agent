"""Runtime 能力报告（RT）：引擎事实基线。

固化运行环境/工具集/模型路由/资源参数/隔离边界/已知差距，作为事后零
运行的签名可验证事实基线（蓝图 §8.2 六问、手册 RT-xx 简化版；评审建议
"为后续准入与 Gateway 身份绑定提供事实基线"）。

- 报告在进程生命周期内生成一次（`build_cached_report`，generatedAt 固定），
  内容不变则幂等；
- `contractId` = sha256(JCS(核心事实，不含 generatedAt)) 前缀 32：事实变化即变，
  避免 digest 自指循环；
- 签名信封复用 Phase 0 codec（蓝图 §9.4 十字段，`build_signature_envelope`
  在 schema/digestprofile 校验通过后原子生成）。
"""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone

from ..config import settings
from ..contracts import (
    build_signature_envelope,
    load_digest_profile,
    load_schema,
)
from .tools import TOOL_DEFINITIONS

PI_RUNTIME_VERSION = "0.3.0"

# 已知差距（如实披露，属于事实的一部分；随功能演进移除）
RT_KNOWN_GAPS = [
    "RT-02: 流式响应/上下文压缩/管道化 ModelCallIntent 未实现（直连 OpenAI 兼容 HTTP）",
    "RT-03: 畸形/超大/乱序协议注入的 NO_VERDICT 拒绝未系统验证",
    "RT-04: 沙箱级进程/网络隔离未实现（当前为工作区目录级限额）",
    "RT-05: 扩展发现/计划任务/RLM 未实现（不存在即不加载）",
    "RT-07: Runtime Driver 幂等 API 未实现",
    "GW-08: 撤销 checkpoint 新鲜度未实现",
    "GW-10: 预算热路径需查询 PostgreSQL（非本地扣减）",
]

_ENVELOPE_BASE = {
    "objectType": "runtime_capability_report",
    "schemaVersion": "2",
    "signatureAlgorithm": "Ed25519",
    "keyId": "sk-runtime",
    "issuer": "runtime-service",
    "issuerWorkloadIdentity": "pi.node",
    "audience": "pi.platform",
    "controlPlaneEpoch": 0,
}

_CACHED: dict | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def core_facts() -> dict:
    """静态事实（不含 generatedAt/contractId）：工具集 + 路由 + 资源 + 隔离 + 差距。"""
    tools = [
        {"name": t["function"]["name"], "requiresApproval": False}
        for t in TOOL_DEFINITIONS
    ]
    tools.sort(key=lambda x: x["name"])
    return {
        "contractVersion": "2",
        "workloadIdentity": "pi.node",
        "runtimeType": "pi-single-node",
        "runtimeVersion": PI_RUNTIME_VERSION,
        "model": {"provider": "cliproxy-local", "name": settings.cliproxy_model},
        "resourceDefaults": {
            "maxTurns": settings.max_turns,
            "maxBudgetTokens": settings.max_budget_tokens,
            "budgetReserveTokens": settings.budget_reserve_tokens,
            "maxToolOutputChars": settings.max_tool_output_chars,
            "llmAttempts": settings.llm_attempts,
        },
        "isolation": {
            "sandbox": "workspace-root-limit",
            "workspacesRoot": str(settings.workspaces_dir),
            "readOnlyDirs": [],
            "networkEnabledForTools": False,
            "processExecutionForTools": True,  # run_command 受限执行（超时+截断）
        },
        "toolCapabilities": tools,
        "knownGaps": list(RT_KNOWN_GAPS),
    }


def _contract_id(facts: dict) -> str:
    blob = json.dumps(facts, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def build_report() -> dict:
    """构造一份完整签名报告（每次调用时间戳变化，payloadDigest 随之变化）。"""
    facts = core_facts()
    report = {**facts, "contractId": _contract_id(facts),
              "generatedAt": _utc_now()}
    schema = load_schema("runtime_capability_report", "2")
    profile = load_digest_profile("runtime_capability_report", "2")
    meta = {**_ENVELOPE_BASE, "signedAt": _utc_now()}
    env, _sig_input, _digest = build_signature_envelope(
        report, schema, profile, meta)
    report["signature"] = {**env, "value": "d" * 128}
    return report


def build_cached_report() -> dict:
    """进程内缓存：生命周期内 generatedAt/签名/对象完全幂等（运行前事实基线）。"""
    global _CACHED
    if _CACHED is None:
        _CACHED = build_report()
    return _CACHED


def clear_cache() -> None:  # 测试用：强制重建
    global _CACHED
    _CACHED = None


def environment_summary() -> dict:
    """环境探测（供报告调用方展示/日志，不进入签名 payload）。"""
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
    }