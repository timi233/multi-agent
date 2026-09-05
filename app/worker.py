"""后台 worker：原子领取 QUEUED 任务、推进状态机、运转 runtime、记录事件与终态。

并发正确性：
- scheduler 在**一个事务**内 FOR UPDATE SKIP LOCKED + QUEUED->RUNNING，提交后才投递线程池，
  避免重复调度同一任务（评审 fix-1）。
- attempt 创建与 ATTEMPT_STARTED 事件与领取同事务（评审 fix-2）。
- 终态写入使用条件更新（仅 RUNNING 可收敛为 SUCCESS/FAILED），cancel 竞争不产生回退。
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


def _run_task(conn, task_id: str) -> None:
    """执行一个已处于 RUNNING 的任务（试图在 RUNNING 竞争条件下收敛）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM pi_tasks WHERE id=%s AND status=%s", (task_id, _RUNNING))
        task = cur.fetchone()
    if not task:
        return  # 已被取消或已终态

    attempt_id = uuid.uuid4().hex[:16]
    trace_id = uuid.uuid4().hex
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pi_attempts (id, task_id, number, status, trace_id) VALUES (%s,%s,1,'CLAIMED',%s)",
            (attempt_id, task_id, trace_id),
        )
        _emit_event(conn, task_id, attempt_id, "ATTEMPT_STARTED",
                    {"traceId": trace_id, "model": task["model"]})
    conn.commit()

    workspace_dir = (settings.workspaces_dir / task["workspace"]).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    try:
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
            # 条件更新：仅 RUNNING 可收敛（防 cancel 竞争回退）
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
        conn.commit()
    except Exception as exc:  # 平台层兜底：异常也收敛为 FAILED（不悬挂）
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_tasks SET status='FAILED', finished_at=now(), updated_at=now(), "
                "error=%s WHERE id=%s AND status=%s",
                (f"{type(exc).__name__}: {exc}", task_id, _RUNNING),
            )
        conn.commit()
        _emit_event(conn, task_id, attempt_id, "ATTEMPT_FAILED",
                    {"error": f"{type(exc).__name__}: {exc}"})
        conn.commit()


class Worker:
    """单机 worker：事务内原子领取（SKIP LOCKED + 状态变更）+ 线程池执行。"""

    def __init__(self, threads: int | None = None):
        self._threads = threads or settings.worker_threads
        self._pool = ThreadPoolExecutor(max_workers=self._threads, thread_name_prefix="pi-worker")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="pi-worker-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            submitted = 0
            conn = connect()
            try:
                with conn.cursor() as cur:
                    # 同事务：加锁后立即领取（QUEUED->RUNNING），提交后才投递执行
                    cur.execute(
                        "SELECT id FROM pi_tasks WHERE status='QUEUED' ORDER BY created_at "
                        "LIMIT %s FOR UPDATE SKIP LOCKED",
                        (self._threads,),
                    )
                    locked = [r["id"] for r in cur.fetchall()]
                    for tid in locked:
                        cur.execute(
                            "UPDATE pi_tasks SET status='RUNNING', started_at=now(), updated_at=now() "
                            "WHERE id=%s AND status='QUEUED'",
                            (tid,),
                        )
                conn.commit()
                for tid in locked:
                    self._pool.submit(self._run_guarded, tid)
                    submitted += 1
            except Exception as exc:  # 领取阶段异常不致命
                print(f"[worker] claim error: {exc}")
            finally:
                conn.close()
            if submitted == 0:
                self._stop.wait(1.0)

    def _run_guarded(self, task_id: str) -> None:
        conn = connect()
        try:
            _run_task(conn, task_id)
        except Exception as exc:
            print(f"[worker] task {task_id} unexpected: {exc}")
        finally:
            conn.close()