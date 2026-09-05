"""编排（G1b）：compile_plan 契约产物 + _run_task 多步执行全流程。

依赖 conftest 的清空：每个用例前后清空 pi_tasks（级联 pi_runs/
pi_attempts/pi_events/gw_*）。
"""
import json

import pytest

from app.db import connect, execute, execute_one
from app.orchestrator import compile_plan
from app.runtime.budget import BudgetDomain, BudgetExceeded
from app.runtime.plans import verified_execution_plan
from app.runtime.run_state import list_runs

TID = "0123456789abcdef"


def _insert_task(prompt="hello", plan=None) -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status, plan) "
                "VALUES (%s, %s, %s, %s, 'RUNNING', %s::jsonb) RETURNING *",
                (TID, "t", prompt, "w",
                 json.dumps(plan) if plan is not None else None))
            return cur.fetchone()


def _run_task(task: dict, fake_run_attempt, monkeypatch=None):
    """假 run_attempt 注入 app.runtime.agent.run_attempt 后跑 worker._run_task。"""
    import app.worker as worker_mod
    from app.runtime import agent as agent_mod
    backup = agent_mod.run_attempt
    agent_mod.run_attempt = fake_run_attempt
    try:
        conn = connect()
        try:
            worker_mod._run_task(conn, task["id"])
        finally:
            conn.close()
    finally:
        agent_mod.run_attempt = backup
    return worker_mod


def test_compile_default_single_step():
    task = {"id": TID, "prompt": "做一件事", "plan": None}
    plan = compile_plan(task)
    assert verified_execution_plan(plan) == []
    assert plan["planKind"] == "INITIAL"
    assert plan["parentExecutionPlanSnapshotId"] is None
    ins = plan["plannedAttemptInputs"]
    assert len(ins) == 1
    assert ins[0]["workflowNodeId"] == "step-1"
    assert ins[0]["runKind"] == "IMPLEMENTATION"
    assert ins[0]["deliverableKind"] == "CODE_CHANGE"
    assert ins[0]["promptContent"] == "做一件事"
    # 与向量生成器同规则：ID 派生含完整不可变前像（此处 taskSpecDigest=None）
    import hashlib
    from app.contracts.codec import jcs
    blob = jcs({
        "taskId": TID, "planKind": "INITIAL",
        "inputs": sorted(ins, key=lambda i: i["plannedAttemptInputId"]),
        "parentExecutionPlanSnapshotId": None, "taskSpecDigest": None,
        "compilerId": "pi-orchestrator", "compilerVersion": "0.1.0",
        "compiledBy": plan["compiledBy"], "compiledAt": plan["compiledAt"],
    })
    assert plan["executionPlanSnapshotId"] == hashlib.sha256(blob).hexdigest()[:32]


def test_compile_multi_step_locked():
    task = {"id": TID, "prompt": "p", "plan": [
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "第一步"},
        {"runKind": "READ_ONLY", "deliverableKind": "READ_ONLY_EVIDENCE",
         "promptContent": "验收", "upstreamBindings": [
             {"slotId": "s", "producerNodeId": "step-1", "required": True}]},
    ]}
    plan = compile_plan(task)
    assert verified_execution_plan(plan) == []
    ins = plan["plannedAttemptInputs"]
    assert [i["workflowNodeId"] for i in ins] == ["step-1", "step-2"]
    assert [i["runKind"] for i in ins] == ["IMPLEMENTATION", "READ_ONLY"]
    assert ins[1]["upstreamBindings"][0]["producerNodeId"] == "step-1"
    assert plan["signature"]["issuer"] == "pi.orchestrator"
    assert plan["signature"]["issuerWorkloadIdentity"] == "pi.orchestrator"
    # 平台每次编译生成新计划（新 input id/新 ID），但语义合法
    plan2 = compile_plan(task)
    assert verified_execution_plan(plan2) == []
    assert plan2["plannedAttemptInputs"][0]["promptContent"] == "第一步"


def test_compile_rejects_bad_steps():
    with pytest.raises(ValueError):
        compile_plan({"id": TID, "prompt": "p", "plan": [
            {"runKind": "SCRIPT", "deliverableKind": "CODE_CHANGE",
             "promptContent": "x"}]})
    with pytest.raises(ValueError):
        compile_plan({"id": TID, "prompt": "p", "plan": [
            {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
             "promptContent": ""}]})
    with pytest.raises(ValueError):
        compile_plan({"id": TID, "prompt": "p", "plan": []})


def _fake_ok(task, **kw):
    return True, "done", None


def _fake_fail(task, **kw):
    return False, "", "tool denied at step"


class _FakeBudgetExceeded:
    """第二臂预算耗尽：直接 raise BudgetExceeded。"""
    def __init__(self):
        self.calls = 0

    def __call__(self, task, **kw):
        self.calls += 1
        raise BudgetExceeded("quota exhausted")


def test_single_step_success_creates_verified_run():
    task = _insert_task(prompt="hello")
    _run_task(task, _fake_ok)
    assert execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))["status"] == "SUCCESS"
    with connect() as conn:
        runs = list_runs(conn, TID)
    assert len(runs) == 1
    assert runs[0]["status"] == "VERIFIED"
    assert runs[0]["step_index"] == 1
    assert runs[0]["run_kind"] == "IMPLEMENTATION"
    assert runs[0]["execution_plan_snapshot_id"] and runs[0]["plan_digest"]
    ev = execute("SELECT event_type FROM pi_events WHERE task_id=%s ORDER BY seq", (TID,))
    types = [r["event_type"] for r in ev]
    assert "TASK_PLAN_COMPILED" in types and "RUN_CREATED" in types
    assert "ATTEMPT_STARTED" in types and "ATTEMPT_FINISHED" in types
    # 步骤终态：attempt TERMINAL_REPORTED + grant SETTLED（无 ACTIVE 残留）
    assert execute_one(
        "SELECT status FROM pi_attempts WHERE task_id=%s", (TID,))["status"] == "TERMINAL_REPORTED"
    assert execute_one(
        "SELECT status FROM gw_budget_grants WHERE task_id=%s", (TID,))["status"] == "SETTLED"


def test_multi_step_serial_execution():
    task = _insert_task(prompt="p", plan=[
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepA"},
        {"runKind": "READ_ONLY", "deliverableKind": "READ_ONLY_EVIDENCE",
         "promptContent": "stepB"},
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepC"},
    ])
    prompts = []

    def fake(task, **kw):
        prompts.append(task["prompt"])
        return True, "ok", None

    _run_task(task, fake)
    assert execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))["status"] == "SUCCESS"
    assert prompts == ["stepA", "stepB", "stepC"]
    with connect() as conn:
        runs = list_runs(conn, TID)
    assert [r["status"] for r in runs] == ["VERIFIED"] * 3
    assert [r["workflow_node_id"] for r in runs] == ["step-1", "step-2", "step-3"]
    grants = execute("SELECT status FROM gw_budget_grants WHERE task_id=%s", (TID,))
    assert len(grants) == 3 and {g["status"] for g in grants} == {"SETTLED"}


def test_step_failure_stops_later_steps():
    task = _insert_task(prompt="p", plan=[
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepA"},
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepB"},
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepC"},
    ])
    calls = []

    def fake(task, **kw):
        calls.append(task["prompt"])
        if task["prompt"] == "stepB":
            return False, "", "tool denied at step"
        return True, "ok", None

    _run_task(task, fake)
    row = execute_one("SELECT status, error FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "FAILED"
    assert "stepB" in row["error"] or "步骤 2" in row["error"]
    assert calls == ["stepA", "stepB"]  # stepC 未执行
    with connect() as conn:
        runs = list_runs(conn, TID)
    assert [r["status"] for r in runs] == ["VERIFIED", "FAILED"]
    assert runs[1]["error_code"] and "tool denied" in runs[1]["error_code"]
    # 两 grant 均已 SETTLED，无 ACTIVE 残留
    grants = execute("SELECT status FROM gw_budget_grants WHERE task_id=%s", (TID,))
    assert len(grants) == 2 and {g["status"] for g in grants} == {"SETTLED"}


def test_step_budget_exhausted_maps_run_and_task():
    task = _insert_task(prompt="p", plan=[
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepA"},
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepB"},
    ])
    seq = {"n": 0}

    def fake(task, **kw):
        seq["n"] += 1
        if seq["n"] == 2:
            raise BudgetExceeded(needed=100, available=10)
        return True, "ok", None

    _run_task(task, fake)
    row = execute_one("SELECT status, error FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "FAILED"
    assert "BUDGET_EXHAUSTED" in row["error"]
    with connect() as conn:
        runs = list_runs(conn, TID)
    assert [r["status"] for r in runs] == ["VERIFIED", "BUDGET_EXHAUSTED"]
    assert "BUDGET" in runs[1]["error_code"]
    grants = execute("SELECT status FROM gw_budget_grants WHERE task_id=%s", (TID,))
    assert {g["status"] for g in grants} == {"SETTLED"}


def test_platform_exception_converges_active_run():
    """评审 block-1：run_attempt 抛非预算异常时活动 Run 须收敛 FAILED
    （不得永久停在 EXECUTING），任务 FAILED。"""
    task = _insert_task(prompt="p", plan=[
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepA"},
    ])

    def fake(task, **kw):
        raise RuntimeError("boom")

    _run_task(task, fake)
    row = execute_one("SELECT status, error FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "FAILED"
    with connect() as conn:
        runs = list_runs(conn, TID)
    assert runs[0]["status"] == "FAILED"
    assert "PLATFORM" in runs[0]["error_code"]
    assert runs[0]["finished_at"] is not None


def test_cancel_between_steps_prevents_further_runs():
    """评审 block-2：第一步执行后任务被取消（cancel 竞争），第二步不得
    创建/执行（每步重新锁定 Task 校验 RUNNING）；已结束的 Run 保持终态。"""
    task = _insert_task(prompt="p", plan=[
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepA"},
        {"runKind": "IMPLEMENTATION", "deliverableKind": "CODE_CHANGE",
         "promptContent": "stepB"},
    ])
    calls = []
    cancelled = {"n": 0}

    def fake(task, **kw):
        calls.append(task["prompt"])
        if len(calls) == 1:
            # 模拟 cancel：worker 之外收敛 Task→CANCELLED（真实场景由 cancel_task 完成）
            with connect() as cc:
                with cc.cursor() as cur:
                    cur.execute(
                        "UPDATE pi_tasks SET status='CANCELLED', finished_at=now() "
                        "WHERE id=%s AND status='RUNNING'", (TID,))
                cc.commit()
            return True, "ok", None
        return True, "ok", None

    _run_task(task, fake)
    row = execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "CANCELLED"  # 不被 worker 覆盖为 SUCCESS
    assert calls == ["stepA"]  # stepB 未执行
    with connect() as conn:
        runs = list_runs(conn, TID)
    assert [r["status"] for r in runs] == ["VERIFIED"]  # 无第二个 Run 创建


def test_plan_empty_list_rejected_by_compile():
    """api 把 plan=[] 以 JSON 空数组入库（评审 should-fix-1），compile 拒收非空约束。"""
    task = _insert_task(prompt="p", plan=[])
    with pytest.raises(ValueError):
        compile_plan(task)


def test_cancel_converges_active_runs():
    """cancel 同事务把活动 Run 收敛为 CANCELLED（蓝图 §8.2 任意非终态→CANCELLED）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                "VALUES (%s,'t','p','w','RUNNING')", (TID,))
            cur.execute(
                "INSERT INTO pi_runs (run_id, task_id, step_index, workflow_node_id, "
                "run_kind, deliverable_kind, execution_plan_snapshot_id, plan_digest, "
                "plan_payload, status) VALUES (%s, %s, 1, 'step-1', 'IMPLEMENTATION', 'CODE_CHANGE', "
                "%s, %s, '{}'::jsonb, 'EXECUTING')",
                ("f" * 16, TID, "a" * 32, "sha256:" + "0" * 64))
    client = TestClient(app)
    r = client.post(f"/api/v1/tasks/{TID}/cancel")
    assert r.status_code == 200
    rows = execute("SELECT status, error_code FROM pi_runs WHERE task_id=%s", (TID,))
    assert rows == [{"status": "CANCELLED", "error_code": "TASK_CANCELLED"}]