"""Run 状态机（蓝图 §8.2 单机简化行进路径）+ pi_runs 表操作。

白名单与任务生命周期（app/control/lifecycle.py）同构，但独立领域：
CREATED → READY → EXECUTING → OUTPUT_STAGED → VERIFYING → VERIFIED
失败/取消映射：EXECUTING → FAILED | BUDGET_EXHAUSTED；
任意非终态 → CANCELLED（任务取消时收敛全部活动 Run）。
NO_VERDICT / HANDOFF_TO_HUMAN / BLOCKED / FAILED_DEPENDENCY /
REPAIR_REQUIRED 为蓝图保留名——本子集不达（顺序执行无依赖等待，
故障注入限定崩溃语义模拟，未实现真实进程 kill/restart）。
"""
from __future__ import annotations

import uuid

ALL_RUN_STATUSES = (
    "CREATED", "READY", "EXECUTING", "OUTPUT_STAGED", "VERIFYING",
    "VERIFIED", "FAILED", "BUDGET_EXHAUSTED", "CANCELLED",
)

_TERMINAL = ("VERIFIED", "FAILED", "BUDGET_EXHAUSTED", "CANCELLED")

_VALID: dict[str, set[str]] = {
    "CREATED": {"READY", "CANCELLED"},
    "READY": {"EXECUTING", "CANCELLED"},
    "EXECUTING": {"OUTPUT_STAGED", "FAILED", "BUDGET_EXHAUSTED", "CANCELLED"},
    "OUTPUT_STAGED": {"VERIFYING", "CANCELLED"},
    "VERIFYING": {"VERIFIED", "CANCELLED"},
    "VERIFIED": set(),
    "FAILED": set(),
    "BUDGET_EXHAUSTED": set(),
    "CANCELLED": set(),
}


class InvalidRunTransition(Exception):
    pass


def assert_run_transition(old: str, new: str) -> None:
    if old not in _VALID or new not in _VALID[old]:
        raise InvalidRunTransition(f"INVALID_RUN_TRANSITION: {old} -> {new}")


def valid_run_transitions(old: str) -> set[str]:
    return set(_VALID.get(old, set()))


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


def insert_run(conn, *, task_id: str, step_index: int, workflow_node_id: str,
               run_kind: str, deliverable_kind: str,
               execution_plan_snapshot_id: str, plan_digest: str,
               plan_payload: dict) -> str:
    """创建步骤 Run（CREATED）。平台唯一写者，失败抛 IntegrityError。"""
    import json

    run_id = uuid.uuid4().hex[:16]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pi_runs (run_id, task_id, step_index, workflow_node_id,
                                 run_kind, deliverable_kind,
                                 execution_plan_snapshot_id, plan_digest,
                                 plan_payload, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'CREATED')
            """,
            (run_id, task_id, step_index, workflow_node_id, run_kind,
             deliverable_kind, execution_plan_snapshot_id, plan_digest,
             json.dumps(plan_payload, ensure_ascii=False)),
        )
    return run_id


def transition_run(conn, run_id: str, new_status: str, *,
                   attempt_id: str | None = None,
                   error_code: str | None = None) -> str:
    """白名单推进 Run 状态（行锁 + 断言 + 条件更新）；返回新状态。"""
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM pi_runs WHERE run_id=%s FOR UPDATE",
                    (run_id,))
        row = cur.fetchone()
        if row is None:
            raise InvalidRunTransition(f"run not found: {run_id}")
        assert_run_transition(row["status"], new_status)
        cur.execute(
            """
            UPDATE pi_runs
            SET status=%s, attempt_id=COALESCE(%s, attempt_id), error_code=%s,
                updated_at=now(),
                finished_at=CASE WHEN %s::text IN
                    ('VERIFIED','FAILED','BUDGET_EXHAUSTED','CANCELLED')
                    THEN now() ELSE finished_at END
            WHERE run_id=%s
            RETURNING status
            """,
            (new_status, attempt_id, error_code, new_status, run_id),
        )
        return cur.fetchone()["status"]


def list_runs(conn, task_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM pi_runs WHERE task_id=%s ORDER BY step_index",
            (task_id,))
        return [dict(r) for r in cur.fetchall()]