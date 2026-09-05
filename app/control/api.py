"""控制面 HTTP API（FastAPI）。"""
from __future__ import annotations

import traceback
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..db import connect, execute, execute_one
from . import models

router = APIRouter(prefix="/api/v1")


def _row_to_task(row: dict) -> models.TaskOut:
    return models.TaskOut(**row)


# ---------- 任务 ----------

@router.post("/tasks", response_model=models.TaskOut, status_code=201)
def create_task(body: models.TaskCreate):
    import json
    import uuid

    task_id = uuid.uuid4().hex[:16]
    workspace = f"task-{task_id}"
    # task 与 TASK_CREATED 事件在同事务写入，payload 由 json.dumps 生成（评审 fix-3）
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pi_tasks (id, title, prompt, workspace, status, model)
                VALUES (%s, %s, %s, %s, 'QUEUED', %s)
                RETURNING *
                """,
                (task_id, body.title, body.prompt, workspace, body.model or settings.cliproxy_model),
            )
            row = cur.fetchone()
            cur.execute(
                "INSERT INTO pi_events (task_id, seq, event_type, payload) "
                "VALUES (%s, 1, 'TASK_CREATED', %s::jsonb)",
                (task_id, json.dumps({"title": body.title}, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()
    return _row_to_task(row)


@router.get("/tasks", response_model=list[models.TaskOut])
def list_tasks(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    rows = execute(
        "SELECT * FROM pi_tasks ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset)
    )
    return [_row_to_task(r) for r in rows]


@router.get("/tasks/{task_id}", response_model=models.TaskOut)
def get_task(task_id: str):
    row = execute_one("SELECT * FROM pi_tasks WHERE id = %s", (task_id,))
    if not row:
        raise HTTPException(404, f"task {task_id} not found")
    return _row_to_task(row)


@router.post("/tasks/{task_id}/cancel", response_model=models.TaskOut)
def cancel_task(task_id: str):
    import json

    # 状态条件更新 + 事件写入在同一连接同一事务；FOR UPDATE 保证读到真实旧状态
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM pi_tasks WHERE id=%s FOR UPDATE", (task_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"task {task_id} not found")
            old = row["status"]
            if old not in ("QUEUED", "RUNNING"):
                raise HTTPException(409, f"cannot cancel task in status {old}")
            cur.execute(
                "UPDATE pi_tasks SET status='CANCELLED', finished_at=now(), updated_at=now() "
                "WHERE id=%s RETURNING *",
                (task_id,),
            )
            row = cur.fetchone()
            cur.execute(
                "INSERT INTO pi_events (task_id, seq, event_type, payload) "
                "VALUES (%s, (SELECT COALESCE(MAX(seq),0)+1 FROM pi_events WHERE task_id=%s), "
                "'TASK_CANCELLED', %s::jsonb)",
                (task_id, task_id, json.dumps({"from": old}, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        conn.close()
    return _row_to_task(row)


# ---------- 事件 ----------

@router.get("/tasks/{task_id}/events", response_model=list[models.EventOut])
def list_events(task_id: str, limit: int = Query(200, ge=1, le=1000)):
    if not execute_one("SELECT 1 FROM pi_tasks WHERE id=%s", (task_id,)):
        raise HTTPException(404, f"task {task_id} not found")
    rows = execute(
        "SELECT * FROM pi_events WHERE task_id=%s ORDER BY seq LIMIT %s", (task_id, limit)
    )
    return [models.EventOut(**r) for r in rows]


# ---------- 工作区文件（只读查看） ----------

def _workspace_root(task_id: str) -> Path:
    row = execute_one("SELECT workspace FROM pi_tasks WHERE id=%s", (task_id,))
    if not row:
        raise HTTPException(404, f"task {task_id} not found")
    root = (settings.workspaces_dir / row["workspace"]).resolve()
    # 工作区根必须位于 workspaces 之下（防路径逃逸）
    if not root.is_relative_to(settings.workspaces_dir.resolve()):
        raise HTTPException(500, "workspace path escape detected")
    return root


def _safe_join(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(400, "path escapes workspace root")
    return target


@router.get("/tasks/{task_id}/workspace", response_model=list[models.WorkspaceEntry])
def list_workspace(task_id: str, path: str = Query(".", max_length=500)):
    root = _workspace_root(task_id)
    target = _safe_join(root, path)
    if not target.exists():
        # 任务刚创建、工作区尚未由 worker 建立时视为空目录
        if path in (".", ""):
            return []
        raise HTTPException(404, f"workspace path {path} not found")
    if target.is_file():
        raise HTTPException(400, "path is a file; use /workspace/file?path=...")
    entries = []
    for child in sorted(target.iterdir()):
        try:
            entries.append(
                models.WorkspaceEntry(
                    path=str(child.relative_to(root)),
                    kind="dir" if child.is_dir() else "file",
                    size=child.stat().st_size if child.is_file() else None,
                )
            )
        except OSError:
            continue
    return entries


@router.get("/tasks/{task_id}/workspace/file")
def read_workspace_file(task_id: str, path: str = Query(..., max_length=1000)):
    root = _workspace_root(task_id)
    target = _safe_join(root, path)
    if not target.is_file():
        raise HTTPException(400, "path is not a file")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(500, f"read failed: {exc}") from exc
    return {"path": path, "content": content}