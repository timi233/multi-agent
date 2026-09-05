"""G2 沙箱硬化测试：命令 deny list/setuid 拒绝/超时整组终止/环境白名单/
cwd 锁定/命令长度/read_only 工具集/路径穿越与 symlink 逃逸拒绝。"""
import ast
import shutil
import time
from pathlib import Path

import pytest

from app.config import settings
from app.runtime.tools import (
    DENIED_COMMANDS,
    MAX_COMMAND_LENGTH,
    ToolError,
    _run_command,
    assert_command_permitted,
    run_tool,
    tool_definitions,
)

@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws

def test_denied_privileged_and_network_commands(ws):
    for cmd in ("sudo ls", "/usr/bin/sudo whoami", "curl http://x",
                "wget -q http://y", "git push origin main", "git clone https://x",
                "git fetch origin", "ping 8.8.8.8",
                "apt-get update", "systemctl restart x", "ssh host", "nc -l 9999"):
        with pytest.raises(ToolError, match=r"denied|拒绝"):
            assert_command_permitted(cmd)
    res = run_tool("run_command", {"command": "curl http://x"}, ws)
    assert res["ok"] is False
    assert "denied" in res["error"]


def test_git_local_readonly_allowed_network_denied():
    """本地 git 工作流允许；外联子命令拒绝（评审 should-fix-1，含全局选项绕过）。"""
    for cmd in ("git status", "git diff", "git log --oneline",
                "git show HEAD", "git branch -a", "git add .", "git commit -m x"):
        assert_command_permitted(cmd)
    for cmd in ("git push origin main", "git pull", "git fetch",
                "git clone https://github.com/x/y.git", "git ls-remote origin",
                "git submodule update", "git remote update",
                "git archive --remote=host:repo main"):
        with pytest.raises(ToolError, match="git 网络外联"):
            assert_command_permitted(cmd)
    # 全局选项绕过路径必须同样拒绝（含无值选项 -p/--paginate，评审 should-fix-1/2）
    for cmd in ("git -C repo push origin main", "git -c k=v fetch",
                "git --git-dir=. pull", "git --work-tree=. clone https://x",
                "git -p push origin main", "git --paginate fetch",
                "git --no-pager pull"):
        with pytest.raises(ToolError, match="git 网络外联"):
            assert_command_permitted(cmd)
    # 全局选项下的本地只读仍允许
    assert_command_permitted("git -C repo status")
    assert_command_permitted("git --git-dir=repo/.git log --oneline")
    assert_command_permitted("git -c color.ui=never diff")
    assert_command_permitted("git -p status")

def test_deny_uses_basename():
    """路径形式攻击：/bin/curl 仍按 basename 拒绝。"""
    with pytest.raises(ToolError, match="denied"):
        assert_command_permitted("/bin/curl http://x")

def test_setuid_flags_rejected_only_for_chmod():
    for cmd in ("chmod u+s ev.sh", "chmod 4755 ev.sh", "chmod 2755 prog",
                "chmod 6755 prog", "chmod a+s ev.sh", "chmod ug+s ev.sh",
                "chmod u+s,u+x ev.sh", "chmod u+sX ev.sh",  # 复合 symbolic 子句
                "chmod --reference=setuid-file target",  # 参考文件模式
                "chmod 2775 prog", "chmod 04755 prog", "chmod 002755 prog",
                "chmod 0004755 prog"):
        with pytest.raises(ToolError, match="setuid"):
            assert_command_permitted(cmd)
    assert_command_permitted("chmod +x script.py")  # 普通 chmod 允许
    assert_command_permitted("chmod 755 script.py")
    assert_command_permitted("chmod 0755 script.py")  # 前导零但无 setuid 位
    assert_command_permitted("chmod u+x,u+w script.py")  # 无 s 的复合 clause 允许
    assert_command_permitted("echo 4755")  # 非 chmod 命令参数不受影响
    assert_command_permitted("chmod +x module-script.py")  # 文件名含 s 不误拒

def test_command_length_limit():
    with pytest.raises(ToolError, match="too long"):
        assert_command_permitted("echo " + "a" * (MAX_COMMAND_LENGTH + 1))

def test_empty_and_bad_command():
    with pytest.raises(ToolError, match="empty"):
        assert_command_permitted("")
    with pytest.raises(ToolError, match="empty"):
        assert_command_permitted("   ")
    with pytest.raises(ToolError, match="bad command"):
        assert_command_permitted("echo 'unterminated")

def test_command_timeout_kills_process_group(ws):
    """超时后整组 SIGKILL（评审 block-2 增强）：父进程与其派生的孙进程
    （sleep 60）都必须随之消亡——sleep pid 写入工作区文件，超时后校验不再存活。"""
    import os
    (ws / "spawn.py").write_text(
        "import subprocess, pathlib\n"
        "p = subprocess.Popen(['sleep', '60'])\n"
        "pathlib.Path('child.pid').write_text(str(p.pid))\n"
        "p.wait()\n")
    start = time.time()
    with pytest.raises(ToolError, match="timed out"):
        _run_command(ws, "python3 spawn.py", timeout=2)
    assert time.time() - start < 15
    pid_file = ws / "child.pid"
    assert pid_file.exists(), "孙进程 pid 未写入（脚本未启动）"
    child_pid = int(pid_file.read_text().strip())
    # 整组终止后孙进程应消失（轮询容忍 zombie 短窗口；Z 状态视为已终止，评审 nit）
    def _alive(pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                fields = f.read().split()
            if len(fields) > 2 and fields[2] == "Z":
                return False  # zombie：已终止待回收
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, FileNotFoundError):
            return False

    deadline = time.time() + 5
    while time.time() < deadline:
        if not _alive(child_pid):
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"孙进程 {child_pid} 在 5s 内未消亡")

def test_minimal_env_whitelist(ws):
    """子进程环境只含 PATH/LANG/HOME（宿主代理/密钥不继承）。"""
    res = _run_command(ws, "python3 -c 'import os; print(sorted(os.environ))'")
    assert res["exit_code"] == 0
    env_names = ast.literal_eval(res["stdout"].strip())
    assert set(env_names) <= {"PATH", "LANG", "HOME"}
    assert "HTTP_PROXY" not in env_names

def test_command_runs_in_workspace_cwd(ws):
    res = _run_command(ws, "pwd")
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == str(ws)

def test_no_shell_metacharacter_execution(ws):
    """无 shell 执行：';' 不会触发第二条命令。"""
    res = _run_command(ws, "echo a; touch pwned")
    assert res["exit_code"] == 0
    assert not (ws / "pwned").exists()
    assert "a" in res["stdout"]

def test_tool_readonly_set_excludes_writers():
    ro = {t["function"]["name"] for t in tool_definitions(read_only=True)}
    assert {"write_file", "edit_file", "run_command"} - ro == {
        "write_file", "edit_file", "run_command"}
    assert {"list_dir", "read_file", "find_files", "grep", "finish"} <= ro
    full = {t["function"]["name"] for t in tool_definitions()}
    assert full - ro == {"write_file", "edit_file", "run_command"}

def test_path_escape_rejected(ws):
    assert run_tool("write_file", {"path": "../evil.txt", "content": "x"}, ws)["ok"] is False
    assert run_tool("read_file", {"path": "../../etc/passwd"}, ws)["ok"] is False
    assert run_tool("list_dir", {"path": ".."}, ws)["ok"] is False

def test_symlink_escape_rejected(ws):
    outside = ws.parent / "outside.txt"
    outside.write_text("secret")
    (ws / "link.txt").symlink_to(outside)
    res = run_tool("read_file", {"path": "link.txt"}, ws)
    assert res["ok"] is False  # resolve 后在工作区外，拒绝

def test_grep_skips_outside_symlinks(ws):
    outside = ws.parent / "secret.txt"
    outside.write_text("TOKEN=abc")
    (ws / "sub").mkdir()
    (ws / "sub" / "link.txt").symlink_to(outside)
    res = run_tool("grep", {"path": ".", "pattern": "TOKEN"}, ws)
    assert res["ok"] is True
    assert res["data"]["matches"] == []


def test_readonly_run_rejects_write_tool_server_side(ws):
    """评审 block-1：READ_ONLY 运行时伪造/异常响应返回 write_file tool call
    也会被服务端授权白名单拒绝；工作区不得被写入。"""
    import json

    from app.runtime.agent import run_attempt

    class GW:
        def __init__(self):
            self._i = 0

        def chat_with_usage(self, messages, tools=None):
            if self._i == 0:
                self._i += 1
                return {"message": {"role": "assistant", "content": "",
                                    "tool_calls": [{
                                        "id": "c1", "type": "function",
                                        "function": {"name": "write_file", "arguments":
                                                     json.dumps({"path": "evil.txt",
                                                                 "content": "x"})}}]}}, None
            return {"message": {"role": "assistant", "content": "done"}}, None

    ok, summary, err = run_attempt(
        task={"prompt": "验收"}, workspace_dir=ws, trace_id="trace",
        emit_event=lambda t, p: None, max_turns=3, gateway=GW(), read_only=True)
    assert ok is True
    assert summary == "done"
    assert not (ws / "evil.txt").exists()


def test_normal_run_allows_write_tool(ws):
    """非只读运行不受白名单影响（write_file 正常可用，基线对照）。"""
    import json

    from app.runtime.agent import run_attempt

    class GW:
        def __init__(self):
            self._i = 0

        def chat_with_usage(self, messages, tools=None):
            if self._i == 0:
                self._i += 1
                return {"message": {"role": "assistant", "content": "",
                                    "tool_calls": [{
                                        "id": "c1", "type": "function",
                                        "function": {"name": "write_file", "arguments":
                                                     json.dumps({"path": "evil.txt",
                                                                 "content": "x"})}}]}}, None
            return {"message": {"role": "assistant", "content": "done"}}, None

    ok, summary, err = run_attempt(
        task={"prompt": "实现"}, workspace_dir=ws, trace_id="trace",
        emit_event=lambda t, p: None, max_turns=3, gateway=GW(), read_only=False)
    assert ok is True
    assert (ws / "evil.txt").exists()


def test_rt_report_sandbox_profile_in_signature_projection():
    """评审 block-3：sandboxProfile 进入不可变投影——篡改 deniedCommands 后
    签名信封 self-digest 校验必须失败；未知字段被 schema 拒绝。"""
    import copy

    from app.contracts.codec import (
        build_signature_envelope, load_digest_profile, load_schema, validate)
    from app.runtime.capabilities import build_cached_report

    report = build_cached_report()
    profile = report["isolation"]["sandboxProfile"]["commandPolicy"]

    tampered = copy.deepcopy(report)
    tampered["isolation"]["sandboxProfile"]["commandPolicy"]["deniedCommands"] = \
        sorted(profile["deniedCommands"] + ["evil-cmd"])
    assert validate(tampered, load_schema("runtime_capability_report", "2")) == []
    # sandboxProfile 进入不可变投影：篡改 deniedCommands 必改变 payloadDigest
    # （旧签名对新内容即失效——与 build_signature_envelope 重算语义配合）
    from app.contracts.codec import payload_digest
    digest_profile = load_digest_profile("runtime_capability_report", "2")
    baseline = report["signature"]["payloadDigest"]
    assert payload_digest(tampered, digest_profile) != baseline

    unknown = copy.deepcopy(report)
    unknown["isolation"]["sandboxProfile"]["commandPolicy"]["bogus"] = 1
    problems = validate(unknown, load_schema("runtime_capability_report", "2"))
    assert any("Additional properties" in p for p in problems)

    # 集合语义：deniedCommands/envWhitelist 排列顺序变化不改变契约 ID（canonical 排序）
    reordered = copy.deepcopy(report)
    cfg = reordered["isolation"]["sandboxProfile"]["commandPolicy"]
    cfg["deniedCommands"] = list(reversed(cfg["deniedCommands"]))
    assert payload_digest(reordered, digest_profile) == baseline
    proc = reordered["isolation"]["sandboxProfile"]["process"]
    proc["envWhitelist"] = list(reversed(proc["envWhitelist"]))
    assert payload_digest(reordered, digest_profile) == baseline
    # git 子命令策略（评审 should-fix-2）：反转顺序不变；内容增删必变
    cfg2 = reordered["isolation"]["sandboxProfile"]["commandPolicy"]
    cfg2["deniedGitSubcommands"] = list(reversed(cfg2["deniedGitSubcommands"]))
    assert payload_digest(reordered, digest_profile) == baseline
    grew = copy.deepcopy(report)
    gcfg = grew["isolation"]["sandboxProfile"]["commandPolicy"]
    gcfg["deniedGitSubcommands"] = sorted(gcfg["deniedGitSubcommands"] + ["prune"])
    assert payload_digest(grew, digest_profile) != baseline