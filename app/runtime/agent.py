"""agent 主循环：LLM 推理 ↔ 工作区工具，直到 finish() 或 LLM 产出最终文本。"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Callable

from ..config import settings
from .gateway import Gateway, GatewayError
from .tools import FINISH_TOOL_NAME, run_tool, tool_definitions

SYSTEM_PROMPT_TEMPLATE = """你是运行在 Pi 单机平台上的软件代理（agent）。
你的任务工作区在：{workspace}
规则：
1. 你只能通过提供的工具操作文件与执行命令；所有路径都是相对工作区根的相对路径。
2. 逐步工作：先探查（list_dir/read_file），再编辑（write_file/edit_file），必要时运行命令验证。
3. 命令要有超时意识；检查 exit_code，失败要修复后重试。
4. 完成全部要求后，调用 finish(summary) 提供简洁的中文总结（做了什么、产出了什么、验证结果）。
5. 严禁访问工作区以外的路径（工具会拒绝）。"""

MAX_TURNS = settings.max_turns


def build_system_prompt(workspace: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(workspace=workspace)


def run_attempt(
    task: dict,
    workspace_dir: Path,
    trace_id: str,
    emit_event: Callable[[str, dict], None],
    max_turns: int = MAX_TURNS,
    gateway: Gateway | None = None,
    budget=None,
    budget_conn=None,
    read_only: bool = False,
) -> tuple[bool, str, str | None]:
    """执行一次 attempt。返回 (ok, summary, error)。

    budget/budget_conn（可选）：BudgetDomain 与连接。启用后每轮 LLM 调用
    按蓝图 GW-xx 记账：调用前持久化预留（RESERVED）→ 发送意图（SENT）→
    响应后结算（SETTLED）/异常失败（FAILED）；每次操作独立 commit。
    预算超限抛 BudgetExceeded（由 worker 映射 BUDGET_EXHAUSTED）。
    read_only=True：G2 沙箱——只暴露只读工具集（list/read/find/grep/finish），
    剔除 write/edit/run_command（REVIEW/READ_ONLY run 步骤）。
    """
    gw = gateway or Gateway(
        base_url=settings.cliproxy_base_url,
        api_key=settings.cliproxy_api_key,
        model=task.get("model") or settings.cliproxy_model,
    )
    tools = tool_definitions(read_only=read_only)
    allowed_tool_names = {t["function"]["name"] for t in tools}  # 服务端授权白名单
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(str(workspace_dir))},
        {"role": "user", "content": task["prompt"]},
    ]

    summary = ""
    error: str | None = None
    finished = False

    for turn in range(1, max_turns + 1):
        emit_event("AGENT_TURN", {"turn": turn, "traceId": trace_id})
        # 每轮最多 settings.llm_attempts 次 Provider 物理请求；每次独立
        # invocation + 调用前预留（评审 fix-blocking-3：隐式重试会无账发送）。
        last_error: str | None = None
        for _phys in range(settings.llm_attempts):
            invocation_id = uuid.uuid4().hex[:16]
            request_digest = hashlib.sha256(
                json.dumps(messages, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:32]
            if budget is not None:
                budget.reserve(budget_conn, invocation_id, request_digest,
                               settings.budget_reserve_tokens)
                budget_conn.commit()
                budget.sent(budget_conn, invocation_id)
                budget_conn.commit()
            try:
                choice, usage = gw.chat_with_usage(messages,
                                                   tools=tools)
            except GatewayError as exc:
                # 发送后不确定结果（超时/断连）：UNKNOWN 保守占额，不释放
                # （评审 fix-blocking-2），预算内重试或终止。
                if budget is not None:
                    budget.unknown(budget_conn, invocation_id)
                    budget_conn.commit()
                last_error = str(exc)
                continue
            if budget is not None:
                actual = (usage or {}).get("total_tokens")
                if actual is None:
                    # Provider 成功但未返回用量事实：不能按 0 消费释放预留
                    # （评审 fix-blocking：不返回 usage 的实现会无限绕过预算），
                    # 以 UNKNOWN 保守占额；choice 仍可用于 agent 流程。
                    budget.unknown(budget_conn, invocation_id)
                else:
                    budget.settle(budget_conn, invocation_id, int(actual))
                budget_conn.commit()
            break
        else:
            error = last_error or "LLM call failed after all attempts"
            return False, "", error

        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        if tool_calls:
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                emit_event("TOOL_CALL", {"turn": turn, "tool": name, "args": raw_args[:2000]})
                if name not in allowed_tool_names:  # G2 评审 block-1：服务端授权强制
                    result = {"ok": False, "error": (
                        f"tool not authorized in this run mode: {name}")}
                else:
                    result = run_tool(name, raw_args, workspace_dir)
                result_text = json.dumps(result, ensure_ascii=False)
                emit_event("TOOL_RESULT", {
                    "turn": turn, "tool": name,
                    "ok": result.get("ok"),
                    "detail": json.dumps(result.get("data") or result.get("error"), ensure_ascii=False)[:2000],
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result_text[: settings.max_tool_output_chars],
                })
                if name == FINISH_TOOL_NAME and result.get("ok"):
                    data = result.get("data") or {}
                    summary = str(data.get("summary", "") or content)
                    finished = True
                    break
            if finished:
                break
            continue  # 下一轮继续推理

        # 无工具调用：LLM 产出最终文本，任务视为完成
        summary = content or task["prompt"]
        finished = True
        break

    if not finished:
        error = f"max turns reached ({max_turns}) without finish"
        return False, "", error

    return True, summary, None