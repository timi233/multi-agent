"""Gateway 预算域测试（蓝图 §18.2/§18.3、手册 GW-03/04/06/07 简化版）。

覆盖：预留/结算/失败释放、超限阻断（含 0 预算 100% 阻断）、链式 Journal
（截断/篡改检测）、同 invocationId 不同 requestDigest 拒绝、settled 后拒绝
预留；worker 层 BUDGET_EXHAUSTED 映射（不调 LLM 的任务级 E2E）。
"""
import uuid

import pytest

from app.db import connect
from app.runtime.budget import BudgetDomain, BudgetError, BudgetExceeded

TASK_ID = "0123456789abcdef"


@pytest.fixture
def conn():
    c = connect()
    yield c
    # 用例间清理：task 级联删除 grants/journal
    with c.cursor() as cur:
        cur.execute("DELETE FROM gw_journal")
        cur.execute("DELETE FROM gw_budget_grants")
        cur.execute("DELETE FROM pi_tasks")
    c.commit()
    c.close()


@pytest.fixture
def task(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'budget-test', 'write a file', 'w-budget', 'QUEUED')",
            (TASK_ID,))
    conn.commit()
    return TASK_ID


def _g(conn, task_id, total=100):
    b = BudgetDomain.create(conn, task_id, "att1", total)
    conn.commit()
    return b


def test_create_and_balance(conn, task):
    b = _g(conn, task, total=100)
    bal = b.balance(conn)
    assert bal == {"total": 100, "consumed": 0, "outstanding": 0,
                   "available": 100, "status": "ACTIVE"}


def test_reserve_then_settle_flow(conn, task):
    """预留 30 → 结算实耗 25：完整释放预留（outstanding→0），实耗计入 consumed，
    余额 = total - consumed = 75（评审 fix-blocking-1：不再双计/泄漏）。"""
    b = _g(conn, task, total=100)
    b.reserve(conn, "inv-1", "digest-1", 30)
    conn.commit()
    bal = b.balance(conn)
    assert bal["outstanding"] == 30 and bal["available"] == 70
    b.sent(conn, "inv-1")
    conn.commit()
    bal = b.balance(conn)
    assert bal["outstanding"] == 30  # SENT 不重复占额
    b.settle(conn, "inv-1", 25)
    conn.commit()
    bal = b.balance(conn)
    assert bal["consumed"] == 25
    assert bal["outstanding"] == 0  # 整笔预留已释放
    assert bal["available"] == 75   # 100 - 25
    b.settle_grant(conn)
    conn.commit()
    assert b.balance(conn)["status"] == "SETTLED"


def test_settle_actual_over_reserve(conn, task):
    """实耗超过预留：consumed 按实耗累加，预留完整释放，无负余额。"""
    b = _g(conn, task, total=100)
    b.reserve(conn, "inv-over", "d", 30)
    conn.commit()
    b.settle(conn, "inv-over", 50)
    conn.commit()
    bal = b.balance(conn)
    assert bal["consumed"] == 50
    assert bal["outstanding"] == 0
    assert bal["available"] == 50


def test_unknown_keeps_reservation(conn, task):
    """发送后不确定（超时/断连）：UNKNOWN 保守占额，不释放不累计（fix-blocking-2）。"""
    b = _g(conn, task, total=100)
    b.reserve(conn, "inv-u", "d", 40)
    conn.commit()
    b.sent(conn, "inv-u")
    conn.commit()
    assert b.balance(conn)["outstanding"] == 40
    b.unknown(conn, "inv-u")
    conn.commit()
    bal = b.balance(conn)
    assert bal["outstanding"] == 40  # 占额保留（Provider 可能已执行）
    assert bal["consumed"] == 0
    assert bal["available"] == 60


def test_reserve_exceeds_budget(conn, task):
    b = _g(conn, task, total=100)
    with pytest.raises(BudgetExceeded, match="BUDGET_EXHAUSTED"):
        b.reserve(conn, "inv-x", "d", 200)
    with pytest.raises(BudgetExceeded):
        b.reserve(conn, "inv-y", "d", 101)   # 边界：100 恰好可用
    b.reserve(conn, "inv-ok", "d", 100)      # 恰好 = total → 允许
    conn.commit()


def test_zero_budget_blocks_immediately(conn, task):
    """GW-07：总预算 0 → 首次调用前 100% 阻断（不触达 LLM）。"""
    b = _g(conn, task, total=0)
    with pytest.raises(BudgetExceeded, match="BUDGET_EXHAUSTED"):
        b.reserve(conn, "inv-0", "d", 1)


def test_fail_releases_reservation(conn, task):
    b = _g(conn, task, total=100)
    b.reserve(conn, "inv-f", "d", 40)
    conn.commit()
    assert b.balance(conn)["outstanding"] == 40
    b.fail(conn, "inv-f")
    conn.commit()
    bal = b.balance(conn)
    assert bal["outstanding"] == 0     # 释放回余额
    assert bal["consumed"] == 0
    assert bal["available"] == 100
    # 释放后同一预算可再次使用
    b.reserve(conn, "inv-g", "d2", 40)
    conn.commit()


def test_chain_verification_ok(conn, task):
    b = _g(conn, task, total=100)
    b.reserve(conn, "i1", "d1", 10)
    conn.commit()
    b.sent(conn, "i1")
    conn.commit()
    b.settle(conn, "i1", 8)
    conn.commit()
    assert b.verify_chain(conn) == []
    assert b.reconcile(conn) == []  # 链 + consumed↔ΣSETTLED 对账均通过


def test_chain_detects_tamper(conn, task):
    b = _g(conn, task, total=100)
    b.reserve(conn, "i1", "d1", 10)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gw_journal SET entry_digest='0'*64 WHERE entry_type='RESERVED'")
    conn.commit()
    problems = b.verify_chain(conn)
    assert any("entry_digest" in p and "不匹配" in p for p in problems)


def test_chain_detects_truncation(conn, task):
    """GW-04：删除 Journal 行后 reconcile 对账/计数暴露（consumed 与条目数）。"""
    b = _g(conn, task, total=100)
    b.reserve(conn, "i1", "d1", 10)
    conn.commit()
    b.settle(conn, "i1", 8)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gw_journal WHERE entry_type='SETTLED'")
    conn.commit()
    problems = b.reconcile(conn)
    assert any("对账不一致" in p for p in problems)
    assert any("条目数不一致" in p for p in problems)


def test_chain_detects_reserved_row_deletion(conn, task):
    """删除 RESERVED 行（恢复余额的路径）：journal_entries 计数锚点检出。"""
    b = _g(conn, task, total=100)
    b.reserve(conn, "i1", "d1", 40)
    conn.commit()
    assert b.balance(conn)["outstanding"] == 40
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gw_journal WHERE entry_type='RESERVED'")
    conn.commit()
    problems = b.reconcile(conn)
    assert any("条目数不一致" in p for p in problems)


def test_duplicate_invocation_digest_mismatch(conn, task):
    """GW-06：同 invocationId 不同 requestDigest 拒绝。"""
    b = _g(conn, task, total=100)
    b.reserve(conn, "inv-same", "digest-A", 10)
    conn.commit()
    with pytest.raises(BudgetError, match="INVOCATION_DIGEST_MISMATCH"):
        b.reserve(conn, "inv-same", "digest-B", 10)
    # 相同 digest 重复预留：UNIQUE(grant, invocation, type) 拦截（DB 层）
    b.settle(conn, "inv-same", 10)
    conn.commit()


def test_settled_grant_rejects_reserve(conn, task):
    b = _g(conn, task, total=100)
    b.reserve(conn, "i1", "d", 10)
    conn.commit()
    b.settle(conn, "i1", 10)
    b.settle_grant(conn)
    conn.commit()
    with pytest.raises(BudgetExceeded):
        b.reserve(conn, "i2", "d", 10)


def test_worker_budget_exhausted_mapping(conn, task, monkeypatch):
    """任务级 E2E（不调 LLM）：0 预算任务 → 首次预留即超限 → FAILED +
    error=BUDGET_EXHAUSTED + 结构化事件 + grant 结算，attempt 收敛。"""
    import app.worker as worker_mod

    from dataclasses import replace
    worker_mod.settings = replace(worker_mod.settings, max_budget_tokens=0)

    with conn.cursor() as cur:  # 模拟已领取：_run_task 按 RUNNING 选取
        cur.execute("UPDATE pi_tasks SET status='RUNNING' WHERE id=%s", (TASK_ID,))
    conn.commit()

    worker_mod._run_task(conn, TASK_ID)

    with conn.cursor() as cur:
        cur.execute("SELECT status, error FROM pi_tasks WHERE id=%s", (TASK_ID,))
        task_row = cur.fetchone()
        cur.execute("SELECT status FROM pi_attempts WHERE task_id=%s", (TASK_ID,))
        att_row = cur.fetchone()
        cur.execute(
            "SELECT status FROM gw_budget_grants WHERE task_id=%s "
            "ORDER BY created_at DESC LIMIT 1", (TASK_ID,))
        grant_row = cur.fetchone()
        cur.execute(
            "SELECT event_type FROM pi_events WHERE task_id=%s "
            "AND event_type='BUDGET_EXHAUSTED'", (TASK_ID,))
        ev = cur.fetchone()
    assert task_row["status"] == "FAILED"
    assert task_row["error"] and "BUDGET_EXHAUSTED" in task_row["error"]
    assert att_row["status"] == "TERMINAL_REPORTED"
    assert grant_row["status"] == "SETTLED"      # 收敛即结算
    assert ev["event_type"] == "BUDGET_EXHAUSTED"  # 结构化 stopReason 事件


def test_worker_budget_grant_chain_clean(conn, task, monkeypatch):
    """正常预算任务完成路径（fake run_attempt 成功）：grant SETTLED、链完整。"""
    import app.runtime.agent as agent_mod
    import app.worker as worker_mod

    def fake_ok(**kw):
        return True, "ok-summary", None

    agent_mod.run_attempt = fake_ok  # 延迟导入从 agent 模块取，patch 需落在 agent
    with conn.cursor() as cur:
        cur.execute("UPDATE pi_tasks SET status='RUNNING' WHERE id=%s", (TASK_ID,))
    conn.commit()
    worker_mod._run_task(conn, TASK_ID)

    with conn.cursor() as cur:
        cur.execute("SELECT status FROM pi_tasks WHERE id=%s", (TASK_ID,))
        assert cur.fetchone()["status"] == "SUCCESS"
        cur.execute("SELECT id, status FROM gw_budget_grants WHERE task_id=%s",
                    (TASK_ID,))
        grant = cur.fetchone()
    assert grant["status"] == "SETTLED"
    b = BudgetDomain(grant["id"])
    assert b.verify_chain(conn) == []
    assert b.reconcile(conn) == []