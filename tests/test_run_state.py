"""Run 状态机白名单（蓝图 §8.2 简化行进路径）+ pi_runs 表操作。"""
import pytest

from app.runtime.run_state import (
    ALL_RUN_STATUSES,
    InvalidRunTransition,
    assert_run_transition,
    insert_run,
    is_terminal,
    list_runs,
    transition_run,
    valid_run_transitions,
)
from app.db import connect

PLAN = {
    "contractVersion": "2",
    "executionPlanSnapshotId": "a" * 32,
    "payloadDigest": "sha256:" + "0" * 64,
    "plannedAttemptInputs": [],
}


def _task_row() -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                "VALUES ('0123456789abcdef', 't', 'p', 'w', 'QUEUED') RETURNING *")
            return cur.fetchone()


def test_white_list_happy_path_success():
    order = ["CREATED", "READY", "EXECUTING", "OUTPUT_STAGED", "VERIFYING", "VERIFIED"]
    for old, new in zip(order, order[1:]):
        assert_run_transition(old, new)  # 不抛


@pytest.mark.parametrize("state", ["CREATED", "READY", "EXECUTING",
                                   "OUTPUT_STAGED", "VERIFYING"])
def test_any_non_terminal_can_cancel(state):
    assert_run_transition(state, "CANCELLED")


def test_invalid_transitions_rejected():
    bad = [
        ("CREATED", "EXECUTING"), ("CREATED", "VERIFIED"),  # 跳步
        ("READY", "OUTPUT_STAGED"), ("VERIFIED", "CANCELLED"),  # 终态回跳
        ("OUTPUT_STAGED", "EXECUTING"),  # 回退
        ("VERIFIED", "FAILED"), ("UNKNOWN", "READY"),  # 未知/终态
        ("EXECUTING", "CREATED"),  # 反向
        ("CANCELLED", "READY"),  # 取消后不可复活
        ("FAILED", "EXECUTING"),
    ]
    for old, new in bad:
        with pytest.raises(InvalidRunTransition):
            assert_run_transition(old, new)


def test_terminal_and_transitions_consistent():
    terminal = {"VERIFIED", "FAILED", "BUDGET_EXHAUSTED", "CANCELLED"}
    assert {s for s in ALL_RUN_STATUSES if is_terminal(s)} == terminal
    for s in terminal:
        assert valid_run_transitions(s) == set()


def test_failure_mapping_from_executing():
    for to in ("FAILED", "BUDGET_EXHAUSTED", "CANCELLED"):
        assert_run_transition("EXECUTING", to)
    assert valid_run_transitions("EXECUTING") == {
        "OUTPUT_STAGED", "FAILED", "BUDGET_EXHAUSTED", "CANCELLED"}


def test_db_insert_and_transition_flow():
    row = _task_row()
    with connect() as conn:
        run_id = insert_run(
            conn, task_id=row["id"], step_index=1, workflow_node_id="step-1",
            run_kind="IMPLEMENTATION", deliverable_kind="CODE_CHANGE",
            execution_plan_snapshot_id="a" * 32,
            plan_digest="sha256:" + "0" * 64, plan_payload=PLAN)
        ctx = conn.cursor()
        ctx.execute("SELECT status, step_index FROM pi_runs WHERE run_id=%s", (run_id,))
        assert ctx.fetchone()["status"] == "CREATED"
        ctx.close()
        assert transition_run(conn, run_id, "READY") == "READY"
        assert transition_run(conn, run_id, "EXECUTING", attempt_id="beefbeefbeefbeef") == "EXECUTING"
        assert transition_run(conn, run_id, "OUTPUT_STAGED") == "OUTPUT_STAGED"
        assert transition_run(conn, run_id, "VERIFYING") == "VERIFYING"
        assert transition_run(conn, run_id, "VERIFIED") == "VERIFIED"
        with pytest.raises(InvalidRunTransition):
            transition_run(conn, run_id, "CANCELLED")  # 终态不可再转
        gotten = list_runs(conn, row["id"])
        assert len(gotten) == 1
        assert gotten[0]["status"] == "VERIFIED"
        assert gotten[0]["attempt_id"] == "beefbeefbeefbeef"
    with connect() as conn:  # 终态行 finished_at 已落
        ctx = conn.cursor()
        ctx.execute("SELECT finished_at FROM pi_runs WHERE run_id=%s", (run_id,))
        assert ctx.fetchone()["finished_at"] is not None
        ctx.close()