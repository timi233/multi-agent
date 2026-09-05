"""agent 主循环：LLM 推理 ↔ 工作区工具，直到 finish() 或 LLM 产出最终文本。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ..config import settings
from .gateway import Gateway, GatewayError
from .tools import FINISH_TOOL_NAME, TOOL_DEFINITIONS, run_tool

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
) -> tuple[bool, str, str | None]:
    """执行一次 attempt。返回 (ok, summary, error)。"""
    gw = gateway or Gateway(
        base_url=settings.cliproxy_base_url,
        api_key=settings.cliproxy_api_key,
        model=task.get("model") or settings.cliproxy_model,
    )
    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(str(workspace_dir))},
        {"role": "user", "content": task["prompt"]},
    ]

    summary = ""
    error: str | None = None
    finished = False

    for turn in range(1, max_turns + 1):
        emit_event("AGENT_TURN", {"turn": turn, "traceId": trace_id})
        try:
            choice = gw.chat(messages, tools=TOOL_DEFINITIONS)
        except GatewayError as exc:
            error = str(exc)
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