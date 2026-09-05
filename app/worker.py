"""后台 worker：原子领取 QUEUED 任务、推进状态机、运转 runtime、记录事件与终态。

并发正确性（经两轮独立评审修订）：
- scheduler 在**一个事务**内 FOR UPDATE SKIP LOCKED + QUEUED->RUNNING，提交后才投递线程池。
- 领取数量受线程池 in-flight 容量约束，不会把队列一次性全部标为 RUNNING。
- attempt 创建与 ATTEMPT_STARTED 事件与领取同事务；终态条件更新（仅 RUNNING 可收敛）。
- 启动时 recover_stale()：把崩溃遗留的「RUNNING 且无 attempt」任务收敛为 FAILED（不悬挂）。
- attempt 总是收敛：任务未被本线程收敛（被 cancel 抢占）时按任务实际终态收敛 attempt 并记录事件。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import settings
from .db import connect
from .runtime.budget import BudgetDomain, BudgetExceeded
from .runtime.run_state import insert_run, transition_run

_RUNNING = "RUNNING"
_TERMINAL = ("SUCCESS", "FAILED", "CANCELLED")
_RUN_TERMINAL = ("VERIFIED", "FAILED", "BUDGET_EXHAUSTED", "CANCELLED")


class _StepFailure(Exception):
    """任务步骤业务失败（非平台异常）：收敛 Run/Attempt 后由外层收敛 Task。"""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


class _Cancelled(Exception):
    """步骤收尾发现 Task 已被取消：中止后续步骤（Task 已终态，无需再收敛）。"""


def _task_state_locked(conn, task_id: str) -> str:
    """SELECT FOR UPDATE 锁读 Task 权威状态（评审 block：步骤终态信封与
    任务终态原子判定，阻塞并发 cancel 的 UPDATE 直至本步提交）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM pi_tasks WHERE id=%s FOR UPDATE", (task_id,))
        row = cur.fetchone()
        return row["status"] if row else "UNKNOWN"


def _converge_cancelled_step(conn, *, task_id: str, attempt_id: str, run_id: str,
                             step_index: int, workspace_dir: Path,
                             budget, stop_reason: str | None,
                             note: str) -> None:
    """取消获胜的统一收尾（评审 block：成功与业务失败共用）：幂等收敛
    Run→CANCELLED（兼容 API 已收敛）、Attempt→TERMINAL_REPORTED、
    Grant→SETTLED、发 ATTEMPT_CANCELLED、签 CANCELLED_CONFIRMED 信封；
    同事务（调用方 commit 后 raise _Cancelled）。"""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pi_runs SET status='CANCELLED', finished_at=now() "
            "WHERE run_id=%s AND status IN ('EXECUTING','OUTPUT_STAGED',"
            "'VERIFYING')", (run_id,))
        cur.execute(
            "UPDATE pi_attempts SET status='TERMINAL_REPORTED', "
            "finished_at=now() WHERE id=%s AND status='CLAIMED'",
            (attempt_id,),
        )
    budget.settle_grant(conn)
    _emit_event(conn, task_id, attempt_id, "ATTEMPT_CANCELLED",
                {"summary": note[:4000], "stepIndex": step_index, "runId": run_id})
    from .runtime.evidence import ingest_step_evidence  # G3
    ingest_step_evidence(
        conn, task_id=task_id, attempt_id=attempt_id, run_id=run_id,
        step_index=step_index, workspace_dir=workspace_dir,
        outcome_class="CANCELLED_CONFIRMED", status="CANCELLED",
        stop_reason=stop_reason)


def _emit_event(conn, task_id: str, attempt_id: str, event_type: str, payload: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pi_events (task_id, attempt_id, seq, event_type, payload)
            VALUES (%s, %s,
                    (SELECT COALESCE(MAX(seq),0)+1 FROM pi_events WHERE task_id=%s),
                    %s, %s::jsonb)
            """,
            (task_id, attempt_id, task_id, event_type, json.dumps(payload, ensure_ascii=False)),
        )


def _converge_attempt(conn, task_id: str, attempt_id: str, task_state: str,
                      note: str = "") -> None:
    """按任务实际终态收敛 attempt（CLAIMED -> TERMINAL_REPORTED）并记录事件；
    任务终态即结算 BudgetGrant（幂等，覆盖 cancel 竞争/lazy 终态路径）。"""
    event_type = "ATTEMPT_CANCELLED" if task_state == "CANCELLED" else "ATTEMPT_FINISHED"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
            "WHERE id=%s AND status='CLAIMED'",
            (attempt_id,),
        )
        cur.execute(
            "UPDATE gw_budget_grants SET status='SETTLED', settled_at=now() "
            "WHERE task_id=%s AND status='ACTIVE'", (task_id,))
        _emit_event(conn, task_id, attempt_id, event_type,
                    {"status": task_state, "note": note})


def _workspace_dir_for(task: dict | None) -> Path | None:
    """异常路径中安全取工作区路径（task 可能未成功读取）。"""
    try:
        if task and task.get("workspace"):
            return (settings.workspaces_dir / task["workspace"]).resolve()
    except Exception:
        pass
    return None


def _ingest_evidence_isolated(task_id: str, attempt_id: str | None,
                              run_id: str | None, step_index: int | None,
                              workspace_dir: Path | None,
                              status: str, stop_reason: str) -> None:
    """独立连接收存终态证据（预算/异常路径：原事务已 aborted）。步骤未真正
    开始（无 run/无 attempt/无工作区）则跳过；收存失败不影响任务收敛。
    评审 block-4：先读真实 Task/Run 终态——取消竞争下任务已 CANCELLED 时
    签发 CANCELLED_CONFIRMED 信封，避免'取消状态 vs 失败信封'矛盾。"""
    if not run_id or not step_index or not workspace_dir or not attempt_id:
        return
    from .runtime.evidence import ingest_step_evidence
    conn = connect()
    try:
        # 评审 block-3：先持任务行锁（SELECT FOR UPDATE）原子判定终态——
        # 阻塞并发的 cancel UPDATE；判定与证据写入同一事务提交，杜绝
        # '读 RUNNING → API 取消 → 落 FAILURE 信封' 的 TOCTOU。
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM pi_tasks WHERE id=%s FOR UPDATE",
                        (task_id,))
            row = cur.fetchone()
            task_state = row["status"] if row else "UNKNOWN"
        if task_state == "CANCELLED":
            outcome, status = "CANCELLED_CONFIRMED", "CANCELLED"
        else:
            outcome = "FAILURE_PLATFORM_PROOF"
        ingest_step_evidence(
            conn, task_id=task_id, attempt_id=attempt_id, run_id=run_id,
            step_index=step_index, workspace_dir=workspace_dir,
            outcome_class=outcome, status=status,
            stop_reason=stop_reason)
        conn.commit()
    except Exception as exc:  # 证据收存为尽力而为：不覆盖任务终态收敛
        print(f"[worker] evidence ingest skipped: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def _fail_run_isolated(run_id: str, status: str, error_code: str) -> None:
    """独立连接将 Run 收敛为终态（幂等：仅非终态可收敛，用于预算耗尽等
    aborted 事务之后的补偿路径）。"""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_runs SET status=%s, error_code=%s, finished_at=now(), "
                "updated_at=now() WHERE run_id=%s AND status NOT IN "
                "('VERIFIED','FAILED','BUDGET_EXHAUSTED','CANCELLED')",
                (status, error_code, run_id),
            )
        conn.commit()
    finally:
        conn.close()


def recover_stale() -> list[str]:
    """启动恢复：把崩溃遗留的 RUNNING 任务收敛为 FAILED（含已建 attempt 但未终态者），
    并把该任务仍在活动的 Run（CREATED/READY/EXECUTING/OUTPUT_STAGED/VERIFYING）
    一并收敛为 FAILED（PLATFORM_RESTART），不悬挂、不与任务终态漂移。"""
    conn = connect()
    stale: list[str] = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM pi_tasks WHERE status='RUNNING'")
            stale = [r["id"] for r in cur.fetchall()]
            for tid in stale:
                cur.execute(
                    "UPDATE pi_tasks SET status='FAILED', finished_at=now(), "
                    "updated_at=now(), error='PLATFORM_RESTART: RUNNING at startup' "
                    "WHERE id=%s AND status='RUNNING'",
                    (tid,),
                )
                cur.execute(
                    "UPDATE pi_runs SET status='FAILED', error_code='PLATFORM_RESTART', "
                    "finished_at=now(), updated_at=now() "
                    "WHERE task_id=%s AND status NOT IN "
                    "('VERIFIED','FAILED','BUDGET_EXHAUSTED','CANCELLED')",
                    (tid,),
                )
                cur.execute(
                    "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
                    "WHERE task_id=%s AND status='CLAIMED'",
                    (tid,),
                )
                cur.execute(
                    "UPDATE gw_budget_grants SET status='SETTLED', settled_at=now() "
                    "WHERE task_id=%s AND status='ACTIVE'",
                    (tid,),
                )
                cur.execute(
                    "INSERT INTO pi_events (task_id, seq, event_type, payload) "
                    "VALUES (%s, (SELECT COALESCE(MAX(seq),0)+1 FROM pi_events WHERE task_id=%s), "
                    "'ATTEMPT_RECOVERED', %s::jsonb)",
                    (tid, tid, json.dumps({"reason": "running-at-startup"}, ensure_ascii=False)),
                )
        conn.commit()
    finally:
        conn.close()
    return stale


def _emit_budget_exhausted(task_id: str, attempt_id: str | None,
                           reason: str) -> None:
    """预算耗尽事实事件（独立连接；评估 should-fix-2——结构化 stopReason）。"""
    try:
        conn = connect()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM pi_tasks WHERE id=%s", (task_id,))
            if cur.fetchone():
                sql = ("INSERT INTO pi_events (task_id, attempt_id, seq, event_type, payload) "
                       "VALUES (%s, %s, COALESCE((SELECT MAX(seq)+1 FROM pi_events WHERE task_id=%s),1), "
                       "'BUDGET_EXHAUSTED', %s)")
                cur.execute(sql, (task_id, attempt_id, task_id,
                                  json.dumps({"reason": reason}, ensure_ascii=False)))
                conn.commit()
    except Exception:
        pass  # 事件尽力而为：主收敛路径不依赖它
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fail_task_isolated(task_id: str, error: str, attempt_id: str | None = None,
                        event_type: str = "ATTEMPT_FAILED") -> bool:
    """用**独立连接**把 RUNNING 任务收敛为 FAILED（含 attempt 收敛与事件）。

    独立连接避免 aborted transaction；条件 UPDATE 仅在状态仍为 RUNNING（未被
    cancel 抢占）时生效；若未命中（已被取消），把 attempt 按任务实际终态收敛
    为 TERMINAL_REPORTED 且不写失败事件（评审 fix-6）。
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_tasks SET status='FAILED', finished_at=now(), updated_at=now(), "
                "error=%s WHERE id=%s AND status='RUNNING' RETURNING id",
                (error, task_id),
            )
            if cur.fetchone() is None:
                # cancel 竞争窗口：任务已被 CANCELLED 等终态抢占，仅收敛 attempt
                if attempt_id:
                    cur.execute("SELECT status FROM pi_tasks WHERE id=%s", (task_id,))
                    row = cur.fetchone()
                    tstate = row["status"] if row else None
                    if tstate == "CANCELLED":
                        cur.execute(
                            "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
                            "WHERE id=%s AND status='CLAIMED'",
                            (attempt_id,),
                        )
                        cur.execute(
                            "UPDATE gw_budget_grants SET status='SETTLED', settled_at=now() "
                            "WHERE task_id=%s AND status='ACTIVE'", (task_id,))
                        _emit_event(conn, task_id, attempt_id, "ATTEMPT_CANCELLED",
                                    {"note": "errored-late-after-cancel"})
                conn.commit()
                return False
            if attempt_id:
                cur.execute(
                    "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
                    "WHERE id=%s AND status='CLAIMED'",
                    (attempt_id,),
                )
            # 任务收敛（任意失败原因）即结算 BudgetGrant（评估 should-fix-1）
            cur.execute(
                "UPDATE gw_budget_grants SET status='SETTLED', settled_at=now() "
                "WHERE task_id=%s AND status='ACTIVE'", (task_id,))
            payload = {"error": error} if event_type == "ATTEMPT_FAILED" else {"reason": error}
            cur.execute(
                "INSERT INTO pi_events (task_id, attempt_id, seq, event_type, payload) "
                "VALUES (%s, %s, (SELECT COALESCE(MAX(seq),0)+1 FROM pi_events WHERE task_id=%s), "
                "%s, %s::jsonb)",
                (task_id, attempt_id, task_id, event_type, json.dumps(payload, ensure_ascii=False)),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def _run_task(conn, task_id: str) -> None:
    """执行一个已处于 RUNNING 的任务；任务可能被 cancel 抢占。

    编译签名 ExecutionPlanSnapshot（旧任务→默认单步计划，兼容回归）→
    逐步执行：每步建 Run（CREATED→READY→EXECUTING）、独立 Attempt 与
    BudgetGrant、调 run_attempt；成功路径 OUTPUT_STAGED→VERIFYING→VERIFIED，
    失败映射 FAILED/BUDGET_EXHAUSTED 并中断后续步骤；任务终态 SUCCESS/FAILED
    由外层统一收敛。任何失败都经独立连接补偿收敛，任务不会停在 RUNNING。
    """
    attempt_id: str | None = None
    current_run_id: str | None = None
    current_step_index: int | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pi_tasks WHERE id=%s AND status=%s", (task_id, _RUNNING))
            task = cur.fetchone()
        if not task:
            return  # 已被取消或已终态

        from .orchestrator import compile_plan  # 延迟导入（避免模块环）

        plan = compile_plan(task)
        trace_id = uuid.uuid4().hex
        with conn.cursor() as cur:
            _emit_event(conn, task_id, None, "TASK_PLAN_COMPILED",
                        {"executionPlanSnapshotId": plan["executionPlanSnapshotId"],
                         "payloadDigest": plan["payloadDigest"],
                         "steps": len(plan["plannedAttemptInputs"])})
        conn.commit()

        workspace_dir = (settings.workspaces_dir / task["workspace"]).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)
        from .runtime.agent import run_attempt  # 延迟导入：测试 monkeypatch agent.run_attempt

        last_attempt_id = None
        for step in plan["plannedAttemptInputs"]:
            attempt_id = None
            # 每步重新锁定 Task 并确认仍 RUNNING（评审 block-2）：避免 cancel 竞争
            # 窗口内继续创建并执行后续 Run（Task→Run 固定加锁顺序，与 cancel 一致）
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM pi_tasks WHERE id=%s FOR UPDATE", (task_id,))
                trow = cur.fetchone()
                if trow is None or trow["status"] != _RUNNING:
                    conn.commit()
                    return  # 已被取消/已终态：不创建新 Run
            step_index = int(step["workflowNodeId"].split("-")[1])
            run_id = insert_run(
                conn, task_id=task_id, step_index=step_index,
                workflow_node_id=step["workflowNodeId"], run_kind=step["runKind"],
                deliverable_kind=step["deliverableKind"],
                execution_plan_snapshot_id=plan["executionPlanSnapshotId"],
                plan_digest=plan["payloadDigest"], plan_payload=plan)
            current_run_id = run_id
            current_step_index = step_index
            with conn.cursor() as cur:
                _emit_event(conn, task_id, None, "RUN_CREATED",
                            {"runId": run_id, "stepIndex": step_index,
                             "workflowNodeId": step["workflowNodeId"],
                             "runKind": step["runKind"],
                             "deliverableKind": step["deliverableKind"]})
            transition_run(conn, run_id, "READY")

            attempt_id = uuid.uuid4().hex[:16]
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pi_attempts (id, task_id, number, status, trace_id) "
                    "VALUES (%s,%s,1,'CLAIMED',%s)",
                    (attempt_id, task_id, trace_id),
                )
                _emit_event(conn, task_id, attempt_id, "ATTEMPT_STARTED",
                            {"traceId": trace_id, "model": task["model"],
                             "runId": run_id, "stepIndex": step_index})
            transition_run(conn, run_id, "EXECUTING", attempt_id=attempt_id)
            budget = BudgetDomain.create(conn, task_id, attempt_id,
                                         settings.max_budget_tokens)
            conn.commit()

            try:
                step_input = dict(task)
                step_input["prompt"] = step["promptContent"]
                ok, summary, error = run_attempt(
                    task=step_input,
                    workspace_dir=workspace_dir,
                    trace_id=trace_id,
                    emit_event=lambda t, p: _emit_event(conn, task_id, attempt_id, t, p),
                    max_turns=settings.max_turns,
                    budget=budget,
                    budget_conn=conn,
                    read_only=step["runKind"] == "READ_ONLY",  # G2：只读运行只暴露只读工具
                )
            except BudgetExceeded as exc:  # 该步预算耗尽：先收敛 Run 再抛给外层收敛 Task
                try:
                    conn.rollback()
                except Exception:
                    pass
                _fail_run_isolated(run_id, "BUDGET_EXHAUSTED", str(exc)[:200])
                raise

            if not ok:  # 步骤业务失败：锁读 Task 权威状态后同事务收敛
                task_state = _task_state_locked(conn, task_id)
                if task_state == "CANCELLED":  # 取消已获胜：统一取消收尾
                    _converge_cancelled_step(
                        conn, task_id=task_id, attempt_id=attempt_id, run_id=run_id,
                        step_index=step_index, workspace_dir=workspace_dir,
                        budget=budget, stop_reason=(error or "cancelled"),
                        note=f"failed-late-after-cancel: {(error or '')[:300]}")
                    conn.commit()
                    raise _Cancelled()
                with conn.cursor() as cur:
                    transition_run(conn, run_id, "FAILED", attempt_id=attempt_id,
                                   error_code=(error or "STEP_FAILED")[:200])
                    cur.execute(
                        "UPDATE pi_attempts SET status='TERMINAL_REPORTED', "
                        "finished_at=now() WHERE id=%s AND status='CLAIMED'",
                        (attempt_id,),
                    )
                    budget.settle_grant(conn)
                    _emit_event(conn, task_id, attempt_id, "ATTEMPT_FINISHED",
                                {"status": "FAILED", "summary": (summary or "")[:4000],
                                 "stepIndex": step_index, "runId": run_id})
                    # 评审 block：步骤终态信封与任务终态 FAILED 同事务原子提交，
                    # 提交后并发 cancel 因 Task 已终态而失效
                    cur.execute(
                        "UPDATE pi_tasks SET status='FAILED', finished_at=now(), "
                        "updated_at=now(), error=%s WHERE id=%s AND status=%s",
                        (f"步骤 {step_index} ({step['workflowNodeId']}) 失败: "
                         f"{(error or 'attempt failed')[:300]}", task_id, _RUNNING))
                from .runtime.evidence import ingest_step_evidence  # G3 终态证据收存
                ingest_step_evidence(
                    conn, task_id=task_id, attempt_id=attempt_id, run_id=run_id,
                    step_index=step_index, workspace_dir=workspace_dir,
                    outcome_class="FAILURE_PLATFORM_PROOF", status="FAILED",
                    stop_reason=(error or "step failed"))
                conn.commit()
                raise _StepFailure(
                    f"步骤 {step_index} ({step['workflowNodeId']}) 失败: "
                    f"{(error or 'attempt failed')[:300]}")

            # 评审 block：先锁读 Task 权威状态（持有行锁），再 atomic 收敛步骤终态：
            # 取消获胜 → Run/Attempt/信封按 CANCELLED（EXECUTING→CANCELLED 合法）；
            # 未取消 → 正常 SUCCESS 收敛（锁保持至 commit，cancel 被阻塞）。
            task_state = _task_state_locked(conn, task_id)
            if task_state == "CANCELLED":
                _converge_cancelled_step(
                    conn, task_id=task_id, attempt_id=attempt_id, run_id=run_id,
                    step_index=step_index, workspace_dir=workspace_dir,
                    budget=budget, stop_reason=None,
                    note="completed-late-after-cancel")
                conn.commit()
                raise _Cancelled()
            with conn.cursor() as cur:
                transition_run(conn, run_id, "OUTPUT_STAGED", attempt_id=attempt_id)
                transition_run(conn, run_id, "VERIFYING")
                transition_run(conn, run_id, "VERIFIED")
                cur.execute(
                    "UPDATE pi_attempts SET status='TERMINAL_REPORTED', "
                    "finished_at=now() WHERE id=%s",
                    (attempt_id,),
                )
                budget.settle_grant(conn)  # 步骤成功终态同事务结算 Grant
                _emit_event(conn, task_id, attempt_id, "ATTEMPT_FINISHED",
                            {"status": "SUCCESS", "summary": (summary or "")[:4000],
                             "stepIndex": step_index, "runId": run_id})
            from .runtime.evidence import ingest_step_evidence  # G3 终态证据收存
            ingest_step_evidence(
                conn, task_id=task_id, attempt_id=attempt_id, run_id=run_id,
                step_index=step_index, workspace_dir=workspace_dir,
                outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                stop_reason=None)
            if step_index == len(plan["plannedAttemptInputs"]):
                # 最后一步：任务 SUCCESS 与信封同事务原子提交（关闭 cancel 窗口）
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE pi_tasks SET status='SUCCESS', finished_at=now(), "
                        "updated_at=now(), error=NULL WHERE id=%s AND status=%s "
                        "RETURNING id", (task_id, _RUNNING))
                    finalized = cur.fetchone() is not None
            else:
                finalized = False
            conn.commit()
            last_attempt_id = attempt_id

        if not finalized and last_attempt_id:
            # 多步任务非最后一步完成但任务仍未终态（下一步循环会重新锁判定）；
            # 维持原语义：此处仅清理遗留（正常路径不可达，防未来回归）
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM pi_tasks WHERE id=%s", (task_id,))
                row = cur.fetchone()
                task_state = row["status"] if row else "UNKNOWN"
            if task_state in _TERMINAL:
                _converge_attempt(conn, task_id, last_attempt_id, task_state,
                                  note="completed-late-after-state-change")
                conn.commit()
    except _Cancelled:  # 步骤收尾发现 Task 已取消：静默返回（任务已终态，不再收敛）
        try:
            conn.rollback()
        except Exception:
            pass
        return
    except _StepFailure as exc:  # 步骤业务失败：任务 FAILED（steps 已各自收敛）
        try:
            conn.rollback()
        except Exception:
            pass
        _fail_task_isolated(task_id, f"TASK: {exc.error}", attempt_id)
    except BudgetExceeded as exc:  # 预算超限：GW-07 100% 阻断，任务 FAILED(BUDGET_EXHAUSTED)
        try:
            conn.rollback()
        except Exception:
            pass
        if current_run_id:
            _fail_run_isolated(current_run_id, "BUDGET_EXHAUSTED", str(exc)[:200])
        # 评审 block：先提交权威 Task 终态（_fail_task_isolated 仅覆盖
        # QUEUED/RUNNING，不覆盖已 CANCELLED），再按持久化终态签发信封——
        # 信封写入锁内读取的最新终态与任务最终状态必一致。
        _fail_task_isolated(task_id, f"BUDGET_EXHAUSTED: {exc}", attempt_id)
        if current_run_id:
            _ingest_evidence_isolated(          # G3：预算终态证据（独立连接）
                task_id, attempt_id, current_run_id, current_step_index,
                _workspace_dir_for(task), "BUDGET_EXHAUSTED", str(exc))
        _emit_budget_exhausted(task_id, attempt_id, str(exc))
    except Exception as exc:  # 平台层兜底：先 rollback 释放行锁，再独立连接统一收敛（防自阻塞）
        try:
            conn.rollback()
        except Exception:
            pass
        if current_run_id:  # 评审 block-1：异常时活动 Run 不得永久停在 EXECUTING
            _fail_run_isolated(current_run_id, "FAILED",
                               f"PLATFORM:{type(exc).__name__}")
        _fail_task_isolated(task_id, f"{type(exc).__name__}: {exc}", attempt_id)
        if current_run_id:
            _ingest_evidence_isolated(          # G3：异常终态证据（独立连接）
                task_id, attempt_id, current_run_id, current_step_index,
                _workspace_dir_for(task), "FAILED", f"{type(exc).__name__}: {exc}")


class Worker:
    """单机 worker：事务内原子领取（SKIP LOCKED + 状态变更）+ 有界线程池执行。"""

    def __init__(self, threads: int | None = None):
        self._threads = threads or settings.worker_threads
        self._pool = ThreadPoolExecutor(max_workers=self._threads, thread_name_prefix="pi-worker")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pi-worker-scheduler", daemon=True)

    def start(self) -> None:
        stale = recover_stale()
        if stale:
            print(f"[worker] recovered {len(stale)} stale RUNNING task(s): {stale}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        inflight: set = set()
        while not self._stop.is_set():
            inflight = {f for f in inflight if not f.done()}
            if self._claim_batch(inflight) == 0:
                self._stop.wait(1.0)

    def _claim_batch(self, inflight: set) -> int:
        """按真实空闲槽位领取一轮任务，返回领取数（可测试）。"""
        capacity = max(0, self._threads - len(inflight))
        if capacity == 0:
            return 0
        submitted = 0
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM pi_tasks WHERE status='QUEUED' ORDER BY created_at "
                    "LIMIT %s FOR UPDATE SKIP LOCKED",
                    (capacity,),
                )
                locked = [r["id"] for r in cur.fetchall()]
                for tid in locked:
                    cur.execute(
                        "UPDATE pi_tasks SET status='RUNNING', started_at=now(), updated_at=now() "
                        "WHERE id=%s AND status='QUEUED'",
                        (tid,),
                    )
            conn.commit()
            remaining = list(range(submitted, len(locked)))  # 尚未尝试投递的下标
            for idx, tid in enumerate(locked):
                if idx < submitted:
                    continue
                try:
                    fut = self._pool.submit(self._run_guarded, tid)
                    inflight.add(fut)
                    submitted += 1
                except Exception as exc:  # 线程池不可用：补偿剩余已领取任务为 FAILED（防悬挂）
                    print(f"[worker] submit failed: {exc}")
                    self._compensate_failed(locked[idx:])
                    break
        except Exception as exc:  # 领取阶段异常不致命
            print(f"[worker] claim error: {exc}")
        finally:
            conn.close()
        return submitted

    def _compensate_failed(self, tids: list[str], reason: str = "SUBMIT_FAILED") -> None:
        """把已领取（RUNNING）但未实际投递执行的任务收敛为 FAILED（条件命中才写事件）。"""
        for tid in tids:
            _fail_task_isolated(tid, reason, event_type="TASK_COMPENSATED")

    def _run_guarded(self, task_id: str) -> None:
        conn = connect()
        try:
            _run_task(conn, task_id)
        except Exception as exc:
            print(f"[worker] task {task_id} unexpected: {exc}")
        finally:
            conn.close()