"""控制面数据类型（Pydantic v2）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="任务标题")
    prompt: str = Field(..., min_length=1, description="给 agent 的任务提示词")
    model: str | None = Field(None, description="覆盖默认模型（可空）")


class TaskOut(BaseModel):
    id: str
    title: str
    prompt: str
    status: str
    error: str | None = None
    model: str | None = None
    workspace: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class EventOut(BaseModel):
    id: int
    task_id: str
    attempt_id: str | None = None
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class WorkspaceEntry(BaseModel):
    path: str
    kind: str  # file|dir
    size: int | None = None
    mtime: datetime | None = None