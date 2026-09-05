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


def test_worker_cancel_during_run(monkeypatch):
    """真实竞争：run_attempt 已启动执行后经 API 取消，worker 迟到收敛且 attempt/事件收敛。"""
    import threading
    import uuid

    from fastapi.testclient import TestClient

    from app.db import connect, execute
    from app import worker as worker_mod
    from app.main import create_app

    tid = uuid.uuid4().hex[:16]
    ws = f"task-{tid}"
    execute(
        "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) "
        "VALUES (%s,'race','prompt',%s,'RUNNING','m')",
        (tid, ws),
    )
    started = threading.Event()
    release = threading.Event()
    outcome = {}

    def fake_run_attempt(**kw):
        started.set()
        assert release.wait(timeout=10), "release not set"
        outcome["summary"] = kw.get("task", {}).get("id")
        return True, "late success", None

    monkeypatch.setattr("app.runtime.agent.run_attempt", fake_run_attempt)

    def run_in_thread():
        conn = connect()
        try:
            worker_mod._run_task(conn, tid)
        finally:
            conn.close()

    t = threading.Thread(target=run_in_thread)
    t.start()
    assert started.wait(timeout=10), "run_attempt 未启动"
    # 经真实 API 取消（验证 cancel 单事务 + TASK_CANCELLED 事件）
    monkeypatch.setattr("app.runtime.agent.run_attempt", fake_run_attempt)
    client = TestClient(create_app(enable_worker=False))
    with client:
        resp = client.post(f"/api/v1/tasks/{tid}/cancel")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "CANCELLED"
    release.set()
    t.join(timeout=15)

    row = execute("SELECT status, error FROM pi_tasks WHERE id=%s", (tid,))[0]
    assert row["status"] == "CANCELLED", "已取消任务不得被 worker 回退为 SUCCESS/FAILED"
    assert row["error"] is None
    # attempt 必须收敛，不得停在 CLAIMED
    att = execute("SELECT status FROM pi_attempts WHERE task_id=%s", (tid,))[0]
    assert att["status"] == "TERMINAL_REPORTED"
    ev_types = [e["event_type"] for e in execute("SELECT event_type FROM pi_events WHERE task_id=%s", (tid,))]
    assert "TASK_CANCELLED" in ev_types, f"缺 TASK_CANCELLED 事件: {ev_types}"
    assert "ATTEMPT_CANCELLED" in ev_types, f"缺 ATTEMPT_CANCELLED 事件: {ev_types}"
    execute("DELETE FROM pi_events WHERE task_id=%s", (tid,))
    execute("DELETE FROM pi_attempts WHERE task_id=%s", (tid,))
    execute("DELETE FROM pi_tasks WHERE id=%s", (tid,))


def test_recover_stale_running_new_task_timeout():
    """启动恢复：RUNNING 且无 attempt 的任务被收敛为 FAILED 并产生恢复事件。"""
    import uuid

    from app.db import execute
    from app import worker as worker_mod

    tid = uuid.uuid4().hex[:16]
    execute(
        "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) "
        "VALUES (%s,'stale','p',%s,'RUNNING','m')",
        (tid, f"task-{tid}"),
    )
    recovered = worker_mod.recover_stale()
    assert tid in recovered
    row = execute("SELECT status, error FROM pi_tasks WHERE id=%s", (tid,))[0]
    assert row["status"] == "FAILED" and "PLATFORM_RESTART" in (row["error"] or "")
    ev = execute("SELECT event_type FROM pi_events WHERE task_id=%s", (tid,))[0]
    assert ev["event_type"] == "ATTEMPT_RECOVERED"
    execute("DELETE FROM pi_events WHERE task_id=%s", (tid,))
    execute("DELETE FROM pi_tasks WHERE id=%s", (tid,))


def test_recover_stale_with_claimed_attempt():
    """启动恢复：RUNNING + CLAIMED attempt（崩溃于 attempt 已建后）也要完整收敛。"""
    import uuid

    from app.db import execute
    from app import worker as worker_mod

    tid = uuid.uuid4().hex[:16]
    att_id = uuid.uuid4().hex[:16]
    execute(
        "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) "
        "VALUES (%s,'stale2','p',%s,'RUNNING','m')",
        (tid, f"task-{tid}"),
    )
    execute(
        "INSERT INTO pi_attempts (id, task_id, number, status, trace_id) "
        "VALUES (%s,%s,1,'CLAIMED',%s)",
        (att_id, tid, uuid.uuid4().hex),
    )
    recovered = worker_mod.recover_stale()
    assert tid in recovered
    assert execute("SELECT status FROM pi_tasks WHERE id=%s", (tid,))[0]["status"] == "FAILED"
    assert execute("SELECT status FROM pi_attempts WHERE id=%s", (att_id,))[0]["status"] == "TERMINAL_REPORTED"
    ev = execute("SELECT event_type FROM pi_events WHERE task_id=%s", (tid,))[0]
    assert ev["event_type"] == "ATTEMPT_RECOVERED"
    execute("DELETE FROM pi_events WHERE task_id=%s", (tid,))
    execute("DELETE FROM pi_attempts WHERE id=%s", (att_id,))
    execute("DELETE FROM pi_tasks WHERE id=%s", (tid,))


def test_fail_isolated_skips_cancelled():
    """补偿收敛仅命中 RUNNING；已 CANCELLED 任务不写错误事件。"""
    import uuid

    from app.db import execute
    from app.worker import _fail_task_isolated

    tid = uuid.uuid4().hex[:16]
    execute(
        "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) "
        "VALUES (%s,'canceled','p',%s,'CANCELLED','m')",
        (tid, f"task-{tid}"),
    )
    assert _fail_task_isolated(tid, "late-failure", event_type="TASK_COMPENSATED") is False
    assert execute("SELECT status FROM pi_tasks WHERE id=%s", (tid,))[0]["status"] == "CANCELLED"
    evs = execute("SELECT event_type FROM pi_events WHERE task_id=%s", (tid,))
    assert evs == [], f"取消任务不应产生补偿事件: {evs}"
    execute("DELETE FROM pi_tasks WHERE id=%s", (tid,))


def test_worker_capacity_limits_claim(monkeypatch):
    """容量控制：线程池满时不再领取（不会把所有 QUEUED 标 RUNNING）。"""
    import uuid
    from concurrent.futures import Future

    from app.db import execute
    from app.worker import Worker

    # 测试共享 PG：先清空任务表避免其他测试残留干扰
    execute("DELETE FROM pi_tasks")

    def make_task():
        tid = uuid.uuid4().hex[:16]
        execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) "
            "VALUES (%s,'cap','p',%s,'QUEUED','m')",
            (tid, f"task-{tid}"),
        )
        return tid

    tids = [make_task() for _ in range(4)]

    class FakePool:
        def submit(self, fn, tid):
            fut = Future()
            inflight_done.add(fut)  # 记录但不完成：模拟一直运行
            return fut

    w = Worker(threads=2)
    inflight_done = set()
    w._pool = FakePool()
    inflight: set = set()
    try:
        first = w._claim_batch(inflight)
        second = w._claim_batch(inflight)
        assert first == 2, "首轮应按容量领取 2 个"
        assert second == 0, "在途未完成时不得再领取"
        # 模拟一个完成：min 清理后应恢复容量
        done = inflight.pop()
        inflight_done.discard(done)
        third = w._claim_batch(inflight)
        assert third == 1, f"释放一个槽位后应再领 1 个，实际 {third}"
        running = {r["id"] for r in execute("SELECT id FROM pi_tasks WHERE status='RUNNING'")}
        queued = {r["id"] for r in execute("SELECT id FROM pi_tasks WHERE status='QUEUED'")}
        assert len(running & set(tids)) == 3, f"RUNNING 应恰为 3: {running & set(tids)}"
        assert len(queued & set(tids)) == 1, "剩余 1 个保持 QUEUED"
    finally:
        for tid in tids:
            execute("DELETE FROM pi_events WHERE task_id=%s", (tid,))
            execute("DELETE FROM pi_tasks WHERE id=%s", (tid,))