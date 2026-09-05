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

_RUNNING = "RUNNING"
_TERMINAL = ("SUCCESS", "FAILED", "CANCELLED")


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
    """按任务实际终态收敛 attempt（CLAIMED -> TERMINAL_REPORTED）并记录事件。"""
    event_type = "ATTEMPT_CANCELLED" if task_state == "CANCELLED" else "ATTEMPT_FINISHED"
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
            "WHERE id=%s AND status='CLAIMED'",
            (attempt_id,),
        )
        _emit_event(conn, task_id, attempt_id, event_type,
                    {"status": task_state, "note": note})


def recover_stale() -> list[str]:
    """启动恢复：把崩溃遗留的 RUNNING 任务收敛为 FAILED（含已建 attempt 但未终态者），不悬挂。"""
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
                    "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
                    "WHERE task_id=%s AND status='CLAIMED'",
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


def _fail_task_isolated(task_id: str, error: str, attempt_id: str | None = None,
                        event_type: str = "ATTEMPT_FAILED") -> bool:
    """用**独立连接**把 RUNNING 任务收敛为 FAILED（含 attempt 收敛与事件）。

    独立连接避免 aborted transaction；条件 UPDATE 仅在状态仍为 RUNNING（未被 cancel
    抢占）时生效，cancel 抢占时不写任何事件（评审 fix-4）。
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
                return False
            if attempt_id:
                cur.execute(
                    "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() "
                    "WHERE id=%s AND status='CLAIMED'",
                    (attempt_id,),
                )
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

    初始化（查询/attempt/事件/首 commit）与主执行都在统一异常边界内；
    任何失败都经独立连接补偿收敛，任务不会停在 RUNNING（评审 fix-3/4）。
    """
    attempt_id: str | None = None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pi_tasks WHERE id=%s AND status=%s", (task_id, _RUNNING))
            task = cur.fetchone()
        if not task:
            return  # 已被取消或已终态

        attempt_id = uuid.uuid4().hex[:16]
        trace_id = uuid.uuid4().hex
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_attempts (id, task_id, number, status, trace_id) "
                "VALUES (%s,%s,1,'CLAIMED',%s)",
                (attempt_id, task_id, trace_id),
            )
            _emit_event(conn, task_id, attempt_id, "ATTEMPT_STARTED",
                        {"traceId": trace_id, "model": task["model"]})
        conn.commit()
    except Exception as exc:  # 初始化失败：先释放已持锁再独立连接补偿，不悬挂
        try:
            conn.rollback()
        except Exception:
            pass
        _fail_task_isolated(task_id, f"INIT: {type(exc).__name__}: {exc}", attempt_id)
        return

    try:
        workspace_dir = (settings.workspaces_dir / task["workspace"]).resolve()
        workspace_dir.mkdir(parents=True, exist_ok=True)

        from .runtime.agent import run_attempt

        ok, summary, error = run_attempt(
            task=task,
            workspace_dir=workspace_dir,
            trace_id=trace_id,
            emit_event=lambda t, p: _emit_event(conn, task_id, attempt_id, t, p),
            max_turns=settings.max_turns,
        )
        final = "SUCCESS" if ok else "FAILED"
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_tasks SET status=%s, finished_at=now(), updated_at=now(), error=%s "
                "WHERE id=%s AND status=%s RETURNING id",
                (final, error, task_id, _RUNNING),
            )
            finalized = cur.fetchone() is not None
            if finalized:
                cur.execute(
                    "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() WHERE id=%s",
                    (attempt_id,),
                )
                _emit_event(conn, task_id, attempt_id, "ATTEMPT_FINISHED",
                            {"status": final, "summary": (summary or "")[:4000]})
            else:
                cur.execute("SELECT status FROM pi_tasks WHERE id=%s", (task_id,))
                row = cur.fetchone()
                task_state = row["status"] if row else "UNKNOWN"
        conn.commit()
        if not finalized:
            if task_state in _TERMINAL:
                _converge_attempt(conn, task_id, attempt_id, task_state,
                                  note="completed-late-after-state-change")
                conn.commit()
    except Exception as exc:  # 平台层兜底：先 rollback 释放行锁，再独立连接统一收敛（防自阻塞）
        try:
            conn.rollback()
        except Exception:
            pass
        _fail_task_isolated(task_id, f"{type(exc).__name__}: {exc}", attempt_id)


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