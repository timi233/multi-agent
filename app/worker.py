"""后台 worker：领取 QUEUED 任务、推进状态机、运转 runtime、记录事件与终态。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import settings
from .db import connect
from .control.lifecycle import assert_transition


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
    conn.commit()


def _claim_and_run(conn, task: dict) -> None:
    """领取单任务并执行（状态机：QUEUED->RUNNING->SUCCESS|FAILED）。"""
    task_id = task["id"]
    # QUEUED -> RUNNING（带条件更新，防并发重复领取）
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE pi_tasks SET status='RUNNING', started_at=now(), updated_at=now() "
            "WHERE id=%s AND status='QUEUED' RETURNING *",
            (task_id,),
        )
        claimed = cur.fetchone()
    conn.commit()
    if not claimed:
        return  # 已被其他 worker 领取

    assert_transition("QUEUED", "RUNNING")
    attempt_id = uuid.uuid4().hex[:16]
    trace_id = uuid.uuid4().hex
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pi_attempts (id, task_id, number, status, trace_id) VALUES (%s,%s,1,'CLAIMED',%s)",
            (attempt_id, task_id, trace_id),
        )
    conn.commit()
    _emit_event(conn, task_id, attempt_id, "ATTEMPT_STARTED",
                {"traceId": trace_id, "model": claimed["model"]})

    workspace_dir = (settings.workspaces_dir / claimed["workspace"]).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    try:
        from .runtime.agent import run_attempt

        ok, summary, error = run_attempt(
            task=dict(claimed),
            workspace_dir=workspace_dir,
            trace_id=trace_id,
            emit_event=lambda t, p: _emit_event(conn, task_id, attempt_id, t, p),
            max_turns=settings.max_turns,
        )
        final = "SUCCESS" if ok else "FAILED"
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_tasks SET status=%s, finished_at=now(), updated_at=now(), error=%s "
                "WHERE id=%s RETURNING status",
                (final, error, task_id),
            )
            cur.execute(
                "UPDATE pi_attempts SET status='TERMINAL_REPORTED', finished_at=now() WHERE id=%s",
                (attempt_id,),
            )
        conn.commit()
        _emit_event(conn, task_id, attempt_id, "ATTEMPT_FINISHED",
                    {"status": final, "summary": (summary or "")[:4000]})
    except Exception as exc:  # 平台层兜底：异常也收敛为 FAILED（不悬挂）
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pi_tasks SET status='FAILED', finished_at=now(), updated_at=now(), "
                "error=%s WHERE id=%s",
                (f"{type(exc).__name__}: {exc}", task_id),
            )
        conn.commit()
        _emit_event(conn, task_id, attempt_id, "ATTEMPT_FAILED",
                    {"error": f"{type(exc).__name__}: {exc}"})


class Worker:
    """单机 worker：SKIP LOCKED 领取 + 线程池执行。"""

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
                    cur.execute(
                        """
                        SELECT * FROM pi_tasks
                        WHERE status='QUEUED'
                        ORDER BY created_at
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                        """,
                        (self._threads,),
                    )
                    candidates = cur.fetchall()
                for task in candidates:
                    self._pool.submit(self._run_guarded, task)
                    submitted += 1
            except Exception as exc:  # 领取阶段异常不致命
                print(f"[worker] claim error: {exc}")
            finally:
                conn.close()
            if submitted == 0:
                self._stop.wait(1.0)

    def _run_guarded(self, task: dict) -> None:
        # 每个 worker 任务独立连接（线程安全）
        conn = connect()
        try:
            _claim_and_run(conn, task)
        except Exception as exc:
            print(f"[worker] task {task['id']} unexpected: {exc}")
        finally:
            conn.close()