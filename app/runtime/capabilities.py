"""Runtime 能力报告（RT）：引擎事实基线。

固化运行环境/工具集/模型路由/资源参数/隔离边界/已知差距，作为**真实
Ed25519 签名**的可验证事实基线（蓝图 §8.2 六问、手册 RT-xx 简化版；评审
建议"为后续准入与 Gateway 身份绑定提供事实基线"）。

- 报告在进程生命周期内生成一次（`build_cached_report`，generatedAt 固定），
  内容不变则幂等；缓存返回 deepcopy，外部无法污染内部事实；
- `contractId` = sha256(JCS(核心事实，不含 generatedAt)) 前缀 32：事实变化即变，
  复用项目 canonical（jcs），无第二套算法；
- 签名信封复用 Phase 0 codec（蓝图 §9.4 十字段，`build_signature_envelope`
  校验后原子生成 `signature_input`）；`signature.value` = 持久化 Runtime
  私钥（data/keys/runtime_ed25519.pem）对 signature_input 的 Ed25519 签名，
  keyId = 公钥指纹——真实可验签（`app.security.keys.verify`）。
"""
from __future__ import annotations

import copy
import hashlib
import platform
from datetime import datetime, timezone

from ..config import settings
from ..contracts import (
    build_signature_envelope,
    jcs,
    load_digest_profile,
    load_schema,
)
from ..security import keys as node_keys
from .tools import (
    DENIED_COMMANDS,
    GIT_NETWORK_SUBCOMMANDS,
    TOOL_DEFINITIONS,
)

PI_RUNTIME_VERSION = "0.3.1"

# 已知差距（如实披露，属于事实的一部分；随功能演进移除）
RT_KNOWN_GAPS = [
    "RT-02: 流式响应/上下文压缩/管道化 ModelCallIntent 未实现（直连 OpenAI 兼容 HTTP）",
    "RT-03: 畸形/超大/乱序协议注入的 NO_VERDICT 拒绝未系统验证",
    "RT-04: 沙箱级进程/网络隔离未实现（当前为命令 deny list + 超时 + 工作区目录级限额；networkIsolation=none-host-network 如实）",
    "RT-05: 扩展发现/计划任务/RLM 未实现（不存在即不加载）",
    "RT-07: Runtime Driver 幂等 API 未实现",
    "GW-08: 撤销 checkpoint 新鲜度未实现",
    "GW-10: 预算热路径需查询 PostgreSQL（非本地扣减）",
]

_ENVELOPE_BASE = {
    "objectType": "runtime_capability_report",
    "schemaVersion": "2",
    "signatureAlgorithm": "Ed25519",
    "keyId": node_keys.key_id(),
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
            "networkEnabledForTools": True,   # 如实：run_command 子进程可触网（无网络隔离，见 knownGaps RT-04）
            "processExecutionForTools": True,  # run_command 受限执行（超时+截断+deny list）
            "sandboxProfile": {
                "type": "workspace-root-limit",
                "commandPolicy": {
                    "shellEnabled": False,       # shlex.split -> argv 直传，无 shell
                    "setuidRejected": True,      # chmod u+s / 4755 / 04755 等拒绝
                    "deniedCommands": sorted(DENIED_COMMANDS),
                    "deniedGitSubcommands": sorted(GIT_NETWORK_SUBCOMMANDS),
                },
                "process": {
                    "commandTimeoutSeconds": settings.command_timeout_seconds,
                    "envWhitelist": ["PATH", "LANG", "HOME"],
                },
                "networkIsolation": "none-host-network",  # 主网络无 netns，如实
                "readOnlyToolsForReadOnlyRuns": True,     # READ_ONLY run 只读工具集
            },
        },
        "toolCapabilities": tools,
        "knownGaps": list(RT_KNOWN_GAPS),
    }


def _contract_id(facts: dict) -> str:
    """事实锚 = sha256(JCS(集合归一化后的核心事实))[:32]；复用项目 canonical
    （jcs）。toolCapabilities/knownGaps/readOnlyDirs 与 DigestProfile 一样视为
    无序集合先排序——枚举顺序变化不改 ID，真实事实内容变化必改（评审 should-fix）。"""
    norm = dict(facts)
    norm["toolCapabilities"] = sorted(facts["toolCapabilities"],
                                      key=lambda t: t["name"])
    norm["knownGaps"] = sorted(facts.get("knownGaps") or [])
    iso = dict(facts.get("isolation") or {})
    iso["readOnlyDirs"] = sorted((facts.get("isolation") or {}).get("readOnlyDirs") or [])
    profile = dict(iso.get("sandboxProfile") or {})
    policy = dict(profile.get("commandPolicy") or {})
    if "deniedCommands" in policy:
        policy["deniedCommands"] = sorted(policy["deniedCommands"])
    if "deniedGitSubcommands" in policy:
        policy["deniedGitSubcommands"] = sorted(policy["deniedGitSubcommands"])
    profile["commandPolicy"] = policy
    proc = dict(profile.get("process") or {})  # 评审 should-fix-2：envWhitelist 亦为集合
    if "envWhitelist" in proc:
        proc["envWhitelist"] = sorted(proc["envWhitelist"])
    profile["process"] = proc
    iso["sandboxProfile"] = profile
    norm["isolation"] = iso
    return hashlib.sha256(jcs(norm)).hexdigest()[:32]


def build_report() -> dict:
    """构造一份完整签名报告（每次调用时间戳变化，payloadDigest 随之变化）。"""
    facts = core_facts()
    report = {**facts, "contractId": _contract_id(facts),
              "generatedAt": _utc_now()}
    schema = load_schema("runtime_capability_report", "2")
    profile = load_digest_profile("runtime_capability_report", "2")
    meta = {**_ENVELOPE_BASE, "signedAt": _utc_now()}
    env, sig_input, _digest = build_signature_envelope(
        report, schema, profile, meta)
    # 真实 Ed25519 签名（评审 block-fix）：对 codec 生成的 signature_input 签名
    report["signature"] = {**env, "value": node_keys.sign(sig_input)}
    return report


def build_cached_report() -> dict:
    """进程内缓存：生命周期内 generatedAt/签名/对象完全幂等（运行前事实基线）。

    返回 deepcopy：外部调用方修改返回对象不会污染缓存事实（评审 should-fix）。"""
    global _CACHED
    if _CACHED is None:
        _CACHED = build_report()
    return copy.deepcopy(_CACHED)


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