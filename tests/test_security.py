"""安全与并发测试：symlink 逃逸、环境清理、cancel-vs-finish 竞争。"""
import os

import pytest

from app.runtime.tools import run_tool


def test_symlink_read_file_escape(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top-secret")
    (tmp_path / "link.txt").symlink_to(outside)
    r = run_tool("read_file", {"path": "link.txt"}, tmp_path)
    assert not r["ok"] and "escapes workspace root" in r["error"]


def test_symlink_grep_skips_outside(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top-secret-content")
    (tmp_path / "link.txt").symlink_to(outside)
    r = run_tool("grep", {"path": ".", "pattern": "top-secret"}, tmp_path)
    assert r["ok"]
    assert all("link.txt" not in m["file"] for m in r["data"]["matches"])


def test_same_prefix_sibling_dir_rejected(tmp_path):
    # 修复前的 startswith 判断会把 <root>x 视为 root 子路径
    base = tmp_path / "ws"
    base.mkdir()
    evil = base.parent / "wsevil"
    evil.mkdir()
    (evil / "pwn.txt").write_text("pwn")
    r = run_tool("read_file", {"path": "../wsevil/pwn.txt"}, base)
    assert not r["ok"] and "escapes workspace root" in r["error"]


def test_grep_sibling_dir_rejected(tmp_path):
    base = tmp_path / "ws"
    base.mkdir()
    evil = base.parent / "wsevil"
    evil.mkdir()
    (evil / "pwn.txt").write_text("secret-token-here")
    r = run_tool("grep", {"path": "../wsevil", "pattern": "secret-token"}, base)
    assert not r["ok"] and "escapes workspace root" in r["error"]


def test_run_command_cleans_env(tmp_path):
    os.environ["PI_TEST_SECRET"] = "should-not-leak"
    try:
        r = run_tool(
            "run_command",
            {"command": "python3 -c 'import os; print(os.environ.get(\"PI_TEST_SECRET\", \"ABSENT\"))'"},
            tmp_path,
        )
        assert r["ok"] and r["data"]["exit_code"] == 0
        assert "ABSENT" in r["data"]["stdout"]
        assert "should-not-leak" not in r["data"]["stdout"]
    finally:
        os.environ.pop("PI_TEST_SECRET", None)


def test_worker_finish_after_cancel_no_rollback(monkeypatch):
    """竞争场景：任务先被 CANCELLED，worker 迟到完成不得回退为 SUCCESS/FAILED。"""
    import uuid

    from app.config import settings
    from app.db import connect, execute
    from app import worker as worker_mod

    tid = uuid.uuid4().hex[:16]
    ws = f"task-{tid}"
    execute(
        "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) "
        "VALUES (%s,'race','prompt',%s,'RUNNING','m')",
        (tid, ws),
    )
    # 模拟 cancel：条件更新为 CANCELLED
    execute(
        "UPDATE pi_tasks SET status='CANCELLED', finished_at=now(), updated_at=now() "
        "WHERE id=%s AND status IN ('QUEUED','RUNNING')",
        (tid,),
    )
    monkeypatch.setattr(
        "app.runtime.agent.run_attempt",
        lambda **kw: (True, "late success", None),
    )
    conn = connect()
    try:
        worker_mod._run_task(conn, tid)  # worker 迟到收敛
    finally:
        conn.close()
    row = execute("SELECT status, error FROM pi_tasks WHERE id=%s", (tid,))[0]
    assert row["status"] == "CANCELLED", "已取消任务不得被回退"
    execute("DELETE FROM pi_tasks WHERE id=%s", (tid,))