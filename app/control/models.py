"""控制面数据类型（Pydantic v2）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="任务标题")
    prompt: str = Field(..., min_length=1, description="给 agent 的任务提示词")
    model: str | None = Field(None, description="覆盖默认模型（可空）")
    plan: list[dict] | None = Field(
        None, description="可选编译前步骤数组（每项 runKind/deliverableKind/"
        "promptContent/upstreamBindings?）；缺省编译为默认单步 IMPLEMENTATION 计划")


class RunOut(BaseModel):
    run_id: str
    task_id: str
    step_index: int
    workflow_node_id: str
    run_kind: str
    deliverable_kind: str
    execution_plan_snapshot_id: str
    plan_digest: str
    attempt_id: str | None = None
    status: str
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


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


class ArtifactOut(BaseModel):
    artifact_id: str
    task_id: str
    run_id: str | None = None
    step_index: int | None = None
    path: str
    digest: str
    size: int
    kind: str
    created_at: datetime


class TerminalEnvelopeOut(BaseModel):
    envelope_id: str
    task_id: str
    attempt_id: str
    run_id: str | None = None
    step_index: int | None = None
    outcome_class: str
    status: str
    verified_ok: bool
    created_at: datetime


class WorkspaceEntry(BaseModel):
    path: str
    kind: str  # file|dir
    size: int | None = None
    mtime: datetime | None = None