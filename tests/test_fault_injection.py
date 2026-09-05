"""故障注入测试（③ 验收：崩溃边界矩阵，手册 GW-03/GW-04 精神 + Phase 0 门槛
"100 次崩溃"的最小可复现子集）。

场景：
- 预留后 kill（不回滚也不结算）→ 占额保留、余额不恢复（崩溃后不可回滚）；
- SENT 后 kill → 每笔预留只消费一次（UNIQUE 兜底，重复结算拒绝）；
- 崩溃恢复（recover_stale）→ 任务 FAILED + Grant 终结 SETTLED、Journal 链完整保留；
- 事务回滚（aborted tx）→ Journal 与余额一致回滚；
- UNKNOWN 占额不可释放 → 后续预留被 100% 阻断（GW-07 侧）。
"""
import uuid

import pytest
import psycopg

from app.db import connect
from app.runtime.budget import BudgetDomain

TASK_ID = "0123456789abcdef"


@pytest.fixture
def conn():
    c = connect()
    yield c
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
            "VALUES (%s, 'fi', 'do', 'w-fi', 'QUEUED')", (TASK_ID,))
    conn.commit()
    return TASK_ID


def _grant(conn, total=100_000):
    b = BudgetDomain.create(conn, TASK_ID, "att-fi", total)
    conn.commit()
    return b


def test_crash_after_reserve_keeps_reservation(conn, task):
    """预留提交（崩溃持久化点）后进程死亡：占额保留、余额不恢复成可用
    （GW-04 精神：预留不可回滚）；Grant 终结结算路径仍闭合。"""
    b = _grant(conn)
    b.reserve(conn, "inv-crash", "d-req", 40_000)
    conn.commit()  # 崩溃点：此处 kill/断电，不执行后续任何操作
    bal = b.balance(conn)
    assert bal["outstanding"] == 40_000
    assert bal["available"] == 60_000  # 未恢复
    b.settle_grant(conn)  # 崩溃恢复后的终结收敛
    conn.commit()
    assert b.balance(conn)["status"] == "SETTLED"


def test_kill_between_sent_and_settle_no_double_consume(conn, task):
    """SENT（发送意图已持久化）后 kill；重启恢复后继续结算——每笔预留只消费
    一次：重复 SETTLED 由 UNIQUE(grant,invocation,type) 拒绝（DB 层兜底）。"""
    b = _grant(conn)
    b.reserve(conn, "inv-s", "d-req", 4096)
    conn.commit()  # kill 点 A
    b.sent(conn, "inv-s")
    conn.commit()  # kill 点 B（发送意图在账）
    b.settle(conn, "inv-s", 1024)
    conn.commit()
    assert b.balance(conn)["consumed"] == 1024
    with pytest.raises(psycopg.errors.UniqueViolation):
        b.settle(conn, "inv-s", 999)  # 重复结算同 invocation → 拒绝
    conn.rollback()  # aborted 事务复位后继续检查
    assert b.balance(conn)["consumed"] == 1024  # 未重复扣减


def test_crash_recovery_settles_grant(conn, task, monkeypatch):
    """worker 崩溃遗留（task RUNNING + grant ACTIVE + 已有预留）→
    recover_stale：任务 FAILED、Grant SETTLED、Journal 链完整保留（预留不可回滚）。"""
    from app.worker import recover_stale

    b = _grant(conn)
    b.reserve(conn, "inv-r", "d-req", 10_000)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pi_tasks SET status='RUNNING', started_at=now() "
            "WHERE id=%s", (TASK_ID,))
        cur.execute(
            "INSERT INTO pi_attempts (id, task_id, number, status, trace_id) "
            "VALUES (%s, %s, 1, 'CLAIMED', %s)",
            (uuid.uuid4().hex[:16], TASK_ID, uuid.uuid4().hex))
    conn.commit()
    stale = recover_stale()
    assert TASK_ID in stale
    with conn.cursor() as cur:
        cur.execute("SELECT status, error FROM pi_tasks WHERE id=%s", (TASK_ID,))
        row = cur.fetchone()
        cur.execute("SELECT status FROM gw_budget_grants WHERE id=%s",
                    (b.grant_id,))
        grant = cur.fetchone()
    assert row["status"] == "FAILED"
    assert "PLATFORM_RESTART" in row["error"]
    assert grant["status"] == "SETTLED"
    assert b.verify_chain(conn) == []  # 链完整
    assert b.balance(conn)["outstanding"] == 10_000  # 预留事实保留（未回滚）


def test_aborted_tx_rolls_back_journal_and_balance(conn, task):
    """aborted 事务：reserve 后回滚（模拟启动失败/连接中断）→ Journal 与
    余额均无残留，grant 状态一致。"""
    b = _grant(conn)
    before = b.balance(conn)
    b.reserve(conn, "inv-ab", "d-req", 777)
    conn.rollback()  # 崩溃回滚：本事务内预留在账与余额更新全部撤销
    bal = b.balance(conn)
    assert bal == before
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS c FROM gw_journal WHERE grant_id=%s",
                    (b.grant_id,))
        assert cur.fetchone()["c"] == 0


def test_unknown_blocks_further_reserve(conn, task):
    """UNKNOWN 占额不可释放：预算被结果不确定的调用占死后，后续预留 100% 阻断
    （GW-07 侧：不重复消费、不超支）。"""
    b = BudgetDomain.create(conn, TASK_ID, "att-u2", 100)
    conn.commit()
    b.reserve(conn, "inv-u", "d1", 40)
    conn.commit()
    b.sent(conn, "inv-u")
    conn.commit()
    b.unknown(conn, "inv-u")  # Provider 结果不确定 → 40 永久占额
    conn.commit()
    assert b.balance(conn)["available"] == 60
    with pytest.raises(Exception, match="BUDGET_EXHAUSTED"):
        b.reserve(conn, "inv-x", "d2", 80)  # 80 > 60 → 拒绝
    with pytest.raises(Exception, match="BUDGET_EXHAUSTED"):
        b.reserve(conn, "inv-y", "d3", 61)  # 边界：消耗恰好超剩余 → 也拒绝