"""工作区文件工具集（安全限定：一切路径解析后必须位于工作区根内）。

对应蓝图 §14.1 附带的"agent 操作本地文件"能力；MVP 裁剪：
- 不引入微沙箱，仅做 root 路径约束 + 命令超时/输出截断（README 中说明风险）。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
from pathlib import Path

from ..config import settings

MAX_OUTPUT = settings.max_tool_output_chars


class ToolError(Exception):
    pass


# ---------- 路径安全 ----------

def _resolve(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if target != root and not str(target).startswith(str(root) + os.sep):
        raise ToolError(f"path escapes workspace root: {rel}")
    return target


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[已截断，共 {len(text)} 字符，仅显示前 {limit} 字符]"


# ---------- 工具实现 ----------

def _list_dir(root: Path, rel: str) -> dict:
    target = _resolve(root, rel)
    if not target.exists():
        raise ToolError(f"not found: {rel}")
    if not target.is_dir():
        raise ToolError(f"not a directory: {rel}")
    entries = []
    for child in sorted(target.iterdir()):
        try:
            st = child.stat()
            entries.append({
                "name": child.name,
                "kind": "dir" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else None,
            })
        except OSError:
            continue
    return {"path": rel, "entries": entries}


def _read_file(root: Path, rel: str, offset: int | None, limit: int | None) -> dict:
    target = _resolve(root, rel)
    if not target.is_file():
        raise ToolError(f"not a file: {rel}")
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as exc:
        raise ToolError(f"read failed: {exc}") from exc
    start = max(0, offset or 0)
    end = len(lines) if limit is None else min(len(lines), start + limit)
    return {"path": rel, "lines": start, "content": "".join(lines[start:end])}


def _write_file(root: Path, rel: str, content: str) -> dict:
    target = _resolve(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": rel, "bytes": len(content.encode("utf-8"))}


def _edit_file(root: Path, rel: str, old_text: str, new_text: str, count: int = 1) -> dict:
    target = _resolve(root, rel)
    if not target.is_file():
        raise ToolError(f"not a file: {rel}")
    text = target.read_text(encoding="utf-8", errors="replace")
    if old_text not in text:
        raise ToolError(f"old_text not found in {rel}")
    n = 0 if count == 0 else (count if count > 0 else 1)
    replaced = text.replace(old_text, new_text, n if n else -1)
    target.write_text(replaced, encoding="utf-8")
    return {"path": rel, "replaced": text.count(old_text), "bytes": len(replaced.encode("utf-8"))}


def _find_files(root: Path, rel: str, pattern: str, max_results: int = 200) -> dict:
    target = _resolve(root, rel)
    if not target.is_dir():
        raise ToolError(f"not a directory: {rel}")
    regex = re.compile(pattern)
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(target):
        # 跳过隐藏目录与缓存
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("__pycache__", ".venv", "node_modules")]
        for name in dirnames + filenames:
            if regex.search(name):
                hits.append(str(Path(dirpath, name).relative_to(root)))
                if len(hits) >= max_results:
                    return {"path": rel, "pattern": pattern, "matches": hits, "truncated": True}
    return {"path": rel, "pattern": pattern, "matches": hits, "truncated": False}


def _grep(root: Path, rel: str, pattern: str, max_results: int = 100) -> dict:
    target = _resolve(root, rel)
    if not target.exists():
        raise ToolError(f"not found: {rel}")
    regex = re.compile(pattern)
    hits: list[dict] = []
    files = [target] if target.is_file() else [
        p for p in target.rglob("*") if p.is_file()
        and not any(part.startswith(".") or part in ("__pycache__", ".venv", "node_modules") for part in p.parts)
    ]
    for p in files:
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    hits.append({"file": str(p.relative_to(root)), "line": i, "text": _clip(line, 300)})
                    if len(hits) >= max_results:
                        return {"path": rel, "pattern": pattern, "matches": hits, "truncated": True}
        except OSError:
            continue
    return {"path": rel, "pattern": pattern, "matches": hits, "truncated": False}


def _run_command(root: Path, command: str) -> dict:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"bad command: {exc}") from exc
    if not argv:
        raise ToolError("empty command")
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=settings.command_timeout_seconds,
            start_new_session=True,  # 独立进程组，超时后可整组终止
        )
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(exc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            pass
        raise ToolError(f"command timed out after {settings.command_timeout_seconds}s: {command}") from exc
    except subprocess.SubprocessError as exc:
        raise ToolError(f"command failed to run: {exc}") from exc
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": _clip(proc.stdout or "", MAX_OUTPUT // 2),
        "stderr": _clip(proc.stderr or "", MAX_OUTPUT // 2),
    }


def _finish(root: Path, summary: str) -> dict:
    return {"summary": summary}


# ---------- schema 与分发 ----------

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir", "description": "列出工作区内目录的内容",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "相对工作区根的路径，默认 '.'"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file", "description": "读取文本文件（可指定起始行与行数）",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "offset": {"type": "integer", "description": "起始行（0 基）"},
                "limit": {"type": "integer", "description": "最多读取行数"}},
                "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file", "description": "写入文件（覆盖）。路径相对工作区根",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file", "description": "在文件中替换文本（默认替换第一次出现；count=0 表示全部替换）",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
                "count": {"type": "integer"}},
                "required": ["path", "old_text", "new_text"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files", "description": "按正则匹配文件名/目录名",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "pattern": {"type": "string"}},
                "required": ["path", "pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep", "description": "在文件中按正则搜索文本行",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "pattern": {"type": "string"}},
                "required": ["path", "pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command", "description": "在工作区目录内运行 shell 命令（如 python3 script.py）。超时会被终止；输出截断",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish", "description": "任务完成；提供最终总结。完成后不再调用其他工具",
            "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
        },
    },
]

_HANDLERS = {
    "list_dir": lambda root, a: _list_dir(root, a.get("path", ".")),
    "read_file": lambda root, a: _read_file(root, a["path"], a.get("offset"), a.get("limit")),
    "write_file": lambda root, a: _write_file(root, a["path"], a.get("content", "")),
    "edit_file": lambda root, a: _edit_file(root, a["path"], a.get("old_text", ""), a.get("new_text", ""), a.get("count", 1)),
    "find_files": lambda root, a: _find_files(root, a.get("path", "."), a.get("pattern", ".*")),
    "grep": lambda root, a: _grep(root, a.get("path", "."), a.get("pattern", "")),
    "run_command": lambda root, a: _run_command(root, a.get("command", "")),
    "finish": lambda root, a: _finish(root, a.get("summary", "")),
}
FINISH_TOOL_NAME = "finish"


def run_tool(name: str, args: dict | str, workspace_dir: Path) -> dict:
    """执行工具并返回标准结果 dict（含 ok/error）。finish 返回 {'summary':...} 且 ok=True。"""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"invalid tool args json: {exc}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "tool args must be an object"}
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        result = handler(workspace_dir, args)
        return {"ok": True, "data": result}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # 工具执行兜底
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}