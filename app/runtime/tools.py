"""工作区文件工具集（安全限定：一切路径解析后必须位于工作区根内）。

对应蓝图 §14.1 附带的"agent 操作本地文件"能力；G2 沙箱硬化后：
- root 路径约束（symlink 感知 resolve）+ 命令 deny list（特权/系统变更/网络
  外联客户端）+ setuid 拒绝 + 命令长度上限 + 超时整组终止 + 最小环境白名单；
- 无 shell 执行（shlex.split -> argv 直传，规避注入）；
- READ_ONLY 运行（蓝图 REVIEW/READ_ONLY run）只暴露只读工具集；
- 网络隔离为 none-host-network（无 netns，见 capabilities RT-04 knownGap）。
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
MAX_COMMAND_LENGTH = 2000

# 执行策略拒绝命令（argv[0] base name 前缀匹配；随版本演进收紧）。
# 类别：特权提升/系统变更、包管理（全局写）、网络外联客户端（断网精神——
# 主机无 netns，以此降级）、跟踪/提权调试器。
DENIED_COMMANDS = frozenset({
    # 特权与系统变更
    "sudo", "su", "visudo", "chown", "passwd", "useradd", "usermod", "groupadd",
    "mount", "umount", "fdisk", "mkfs", "mkfs.ext4", "iptables", "iptables-restore",
    "firewall-cmd", "systemctl", "service", "reboot", "shutdown", "halt",
    "poweroff", "init", "swapoff", "swapon",
    # 全局包管理
    "apt", "apt-get", "dpkg", "yum", "dnf", "pacman", "brew",
    # 网络外联客户端（git 单独按子命令策略：本地只读允许、外联拒绝）
    "curl", "wget", "nc", "ncat", "telnet", "ssh", "scp", "sftp", "ftp",
    "ping", "traceroute", "dig", "nslookup", "host",
    # 调试/跟踪（host 敏感）
    "strace", "ltrace", "bpftrace", "perf", "gdb", "tcpdump",
})

# git 网络/外联子命令：拒绝（status/diff/log/show 等本地只读与工作区操作允许；
# remote/archive 因含外联入口一并拒绝——README 披露）。git 全局选项
# （-C/-c/--git-dir/--work-tree 等）由 _git_subcommand 跳过后再判定。
GIT_NETWORK_SUBCOMMANDS = frozenset({
    "push", "fetch", "pull", "clone", "ls-remote", "submodule",
    "remote", "archive",
})

_GIT_VALUE_OPTS = frozenset({"-C", "--git-dir", "--work-tree", "--exec-path", "-c"})
_GIT_FLAG_OPTS = frozenset({"-p", "--paginate", "--no-pager", "-v", "--bare"})


def _git_subcommand(argv: list[str]) -> str:
    """跳过 git 全局选项后返回真实子命令（评审 should-fix：区分带值选项
    -C/-c/--git-dir 与无值选项 -p/--paginate，防 'git -p push' 绕过）。"""
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in _GIT_VALUE_OPTS:
            i += 2
            continue
        if a in _GIT_FLAG_OPTS:
            i += 1
            continue
        if a.startswith("--") and "=" in a and a.split("=", 1)[0] in {
                "--git-dir", "--work-tree", "--exec-path"}:
            i += 1
            continue
        if a.startswith("-"):
            i += 1  # 其它未知短选项：保守向后继续解析（首个非选项即子命令）
            continue
        return a
    return ""

# chmod 的 setuid/setgid 位（数字模式掩码）与 symbolic 标记
_SETID_MODES = 0o6000


class ToolError(Exception):
    pass


def _deny_setuid(argv: list[str], command: str) -> None:
    """setuid/setgid 拒绝——仅解析 chmod（评审 block-2 加强）：数字模式按
    mode & 0o6000 判定；symbolic 按逗号拆分 clause，任何赋 's/S' 位（含
    u+s,u+x、u+sX 形式）一律拒绝（-s 移除也保守拒绝，防语义混淆）；
    --reference 参考文件模式参数拒绝。其余命令不受影响。"""
    if os.path.basename(argv[0]) != "chmod":
        return
    for arg in argv[1:]:
        if arg in ("--", "-R", "-r", "-v", "-c", "-f"):
            continue
        if arg.startswith("--reference"):
            raise ToolError(f"setuid/setgid 被沙箱策略拒绝（--reference）: {command}")
        # GNU chmod 接受任意长度八进制（含任意前导零，如 04755/0004755）——评审
        if re.fullmatch(r"[0-7]+", arg):
            if int(arg, 8) & _SETID_MODES:
                raise ToolError(f"setuid/setgid 被沙箱策略拒绝: {command}")
            continue
        # symbolic 复合子句：逗号拆分逐段判定（u+s,u+x / u+sX / a+s 均覆盖）
        for clause in arg.split(","):
            clause = clause.strip()
            if re.fullmatch(r"[ugoa]*[+\-=][rwxXst]*", clause) and "s" in clause:
                raise ToolError(f"setuid/setgid 被沙箱策略拒绝: {command}")


def assert_command_permitted(command: str) -> None:
    """沙箱执行前审核：非空、长度受限、argv[0] 不在 deny list、git 外联
    子命令拒绝、chmod setuid 拒绝。"""
    if not command or not command.strip():
        raise ToolError("empty command")
    if len(command) > MAX_COMMAND_LENGTH:
        raise ToolError(f"command too long ({len(command)} > {MAX_COMMAND_LENGTH})")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"bad command: {exc}") from exc
    if not argv:
        raise ToolError("empty command")
    base = os.path.basename(argv[0])
    if base in DENIED_COMMANDS:
        raise ToolError(f"command denied by sandbox policy: {argv[0]}")
    if base == "git" and _git_subcommand(argv) in GIT_NETWORK_SUBCOMMANDS:
        raise ToolError(f"git 网络外联被沙箱策略拒绝: {_git_subcommand(argv)}")
    _deny_setuid(argv, command)


def tool_definitions(read_only: bool = False) -> list[dict]:
    """工具定义；READ_ONLY 运行剔除写/执行类（review READ_ONLY_EVIDENCE 步骤）。"""
    denied = {"write_file", "edit_file", "run_command"}
    return [t for t in TOOL_DEFINITIONS
            if not (read_only and t["function"]["name"] in denied)]


# ---------- 路径安全 ----------

def _resolve(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if target != root and not target.is_relative_to(root):
        raise ToolError(f"path escapes workspace root: {rel}")
    return target


def _inside(root: Path, target: Path) -> bool:
    """resolve 后的 target 是否位于 root 内（symlink 感知）。"""
    return target == root or target.is_relative_to(root)


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
    candidates = [target] if target.is_file() else [
        p for p in target.rglob("*") if p.is_file()
        and not any(part.startswith(".") or part in ("__pycache__", ".venv", "node_modules") for part in p.parts)
    ]
    for p in candidates:
        # symlink 感知：每个候选文件 resolve 后必须仍位于工作区内
        real = p.resolve()
        if not _inside(root, real):
            continue
        try:
            for i, line in enumerate(real.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if regex.search(line):
                    hits.append({"file": str(real.relative_to(root)), "line": i, "text": _clip(line, 300)})
                    if len(hits) >= max_results:
                        return {"path": rel, "pattern": pattern, "matches": hits, "truncated": True}
        except OSError:
            continue
    return {"path": rel, "pattern": pattern, "matches": hits, "truncated": False}


def _run_command(root: Path, command: str,
                 timeout: int | None = None) -> dict:
    assert_command_permitted(command)  # G2 沙箱策略：deny list/setuid/长度
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"bad command: {exc}") from exc
    if not argv:
        raise ToolError("empty command")
    # 环境清洗：只保留最小 PATH，不继承宿主环境（防进程凭据/API key 泄露）
    clean_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(root)}
    limit = timeout if timeout is not None else settings.command_timeout_seconds
    proc = None
    try:
        proc = subprocess.Popen(  # 评审 block-2：Popen 保留 pid，超时后整组 kill
            argv, cwd=root, env=clean_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True)  # 独立进程组，超时后可整组终止（含孙进程）
        try:
            stdout, stderr = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # SIGKILL 已发出；不悬挂主流程
            raise ToolError(f"command timed out after {limit}s: {command}")
    except ToolError:
        raise
    except subprocess.SubprocessError as exc:
        raise ToolError(f"command failed to run: {exc}") from exc
    finally:
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    return {
        "command": command,
        "exit_code": proc.returncode,
        "stdout": _clip(stdout or "", MAX_OUTPUT // 2),
        "stderr": _clip(stderr or "", MAX_OUTPUT // 2),
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