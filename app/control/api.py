"""控制面 HTTP API（FastAPI）。"""
from __future__ import annotations

import traceback
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..db import connect, execute, execute_one
from ..runtime.capabilities import build_cached_report
from . import models

router = APIRouter(prefix="/api/v1")


def _row_to_task(row: dict) -> models.TaskOut:
    return models.TaskOut(**row)


# ---------- Runtime ----------

@router.get("/runtime/capabilities")
def runtime_capabilities() -> dict:
    """Runtime 能力报告（RT）：签名可验证的引擎事实基线（进程内缓存，幂等）。"""
    return build_cached_report()


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
                INSERT INTO pi_tasks (id, title, prompt, workspace, status, model, plan)
                VALUES (%s, %s, %s, %s, 'QUEUED', %s, %s::jsonb)
                RETURNING *
                """,
                (task_id, body.title, body.prompt, workspace,
                 body.model or settings.cliproxy_model,
                 json.dumps(body.plan, ensure_ascii=False) if body.plan is not None
                 else None),
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
            cur.execute(  # 取消同事务结算 Grant：worker 即使不再运行也不留 ACTIVE
                "UPDATE gw_budget_grants SET status='SETTLED', settled_at=now() "
                "WHERE task_id=%s AND status='ACTIVE'",
                (task_id,),
            )
            cur.execute(  # 取消同事务收敛任务全部活动 Run（蓝图 §8.2 任意非终态→CANCELLED）
                "UPDATE pi_runs SET status='CANCELLED', error_code='TASK_CANCELLED', "
                "finished_at=now(), updated_at=now() "
                "WHERE task_id=%s AND status IN "
                "('CREATED','READY','EXECUTING','OUTPUT_STAGED','VERIFYING')",
                (task_id,),
            )
        conn.commit()
    finally:
        conn.close()
    return _row_to_task(row)


# ---------- Run（编排步骤执行记录，蓝图 §8.2） ----------

@router.get("/tasks/{task_id}/runs", response_model=list[models.RunOut])
def list_runs(task_id: str):
    if not execute_one("SELECT 1 FROM pi_tasks WHERE id=%s", (task_id,)):
        raise HTTPException(404, f"task {task_id} not found")
    rows = execute(
        "SELECT run_id, task_id, step_index, workflow_node_id, run_kind, "
        "deliverable_kind, execution_plan_snapshot_id, plan_digest, attempt_id, "
        "status, error_code, created_at, updated_at, finished_at "
        "FROM pi_runs WHERE task_id=%s ORDER BY step_index",
        (task_id,),
    )
    return [models.RunOut(**r) for r in rows]


# ---------- Artifact / Evidence（蓝图 §6.9/§11.1，G3） ----------

@router.get("/tasks/{task_id}/artifacts", response_model=list[models.ArtifactOut])
def list_artifacts(task_id: str):
    if not execute_one("SELECT 1 FROM pi_tasks WHERE id=%s", (task_id,)):
        raise HTTPException(404, f"task {task_id} not found")
    rows = execute(
        "SELECT artifact_id, task_id, run_id, step_index, path, digest, size, "
        "kind, created_at FROM pi_artifacts WHERE task_id=%s "
        "ORDER BY step_index, path",
        (task_id,),
    )
    return [models.ArtifactOut(**r) for r in rows]


@router.get("/tasks/{task_id}/terminal-envelopes",
            response_model=list[models.TerminalEnvelopeOut])
def list_terminal_envelopes(task_id: str):
    if not execute_one("SELECT 1 FROM pi_tasks WHERE id=%s", (task_id,)):
        raise HTTPException(404, f"task {task_id} not found")
    rows = execute(
        "SELECT envelope_id, task_id, attempt_id, run_id, step_index, "
        "outcome_class, status, verified_ok, created_at "
        "FROM pi_terminal_envelopes WHERE task_id=%s ORDER BY step_index",
        (task_id,),
    )
    return [models.TerminalEnvelopeOut(**r) for r in rows]


# ---------- Git 交付（蓝图 §10.10/§11 单机子集，G5） ----------

@router.post("/tasks/{task_id}/staging:commit")
def commit_staging(task_id: str, body: models.StagingCommitRequest) -> dict:
    """确定性本地 git 交付：CommitBundle（assembler 签名）+ 读回证据
    GitStagingResult（git-stager 签名）。同 opKey 幂等复用既有证明。"""
    from ..runtime.gitstager import GitStagingError, stage_commit
    if not execute_one("SELECT 1 FROM pi_tasks WHERE id=%s", (task_id,)):
        raise HTTPException(404, f"task {task_id} not found")
    if body.operationIdempotencyKey is not None and (
            len(body.operationIdempotencyKey) != 32 or
            not all(c in "0123456789abcdef" for c in body.operationIdempotencyKey)):
        raise HTTPException(422, "operationIdempotencyKey 必须为 32hex")
    try:
        return stage_commit(task_id, attempt_id=body.attemptId,
                            op_key=body.operationIdempotencyKey)
    except GitStagingError as exc:
        msg = str(exc)
        if "无产物" in msg:
            raise HTTPException(409, msg) from exc
        raise HTTPException(500, msg) from exc


@router.get("/tasks/{task_id}/staging-results",
            response_model=list[models.GitStagingResultOut])
def list_staging_results(task_id: str):
    if not execute_one("SELECT 1 FROM pi_tasks WHERE id=%s", (task_id,)):
        raise HTTPException(404, f"task {task_id} not found")
    rows = execute(
        "SELECT result_id, task_id, commit_bundle_id, commit_bundle_digest, "
        "operation_idempotency_key, repository_id, candidate_ref, "
        "applied_commit_id, git_staging_epoch, verified_ok, created_at "
        "FROM pi_git_staging_results WHERE task_id=%s "
        "ORDER BY git_staging_epoch",
        (task_id,),
    )
    return [models.GitStagingResultOut(**r) for r in rows]


# ---------- Skill 供应链（蓝图 §12.3 单机子集，G4） ----------

@router.post("/skills/packages", response_model=models.SkillPackageOut)
def create_skill_package(body: models.SkillPackageCreate):
    from app.db import connect
    from app.runtime.skills import register_package
    conn = connect()
    try:
        pkg = register_package(conn, name=body.name, version=body.version,
                               source_dir=body.source_dir,
                               description=body.description)
        conn.commit()
    finally:
        conn.close()
    row = execute_one(
        "SELECT skill_package_id, name, version, description, package_digest, "
        "created_at FROM pi_skill_packages WHERE skill_package_id=%s",
        (pkg["skill_package_id"],))
    return models.SkillPackageOut(**row)


@router.post("/skills/bundles:build")
def build_skill_bundle_api(body: models.BundleBuildRequest):
    from app.db import connect
    from app.runtime import skills as skills_mod
    conn = connect()
    try:
        refs = skills_mod.resolve_package_refs(conn, body.package_source_dirs)
        build = skills_mod.build_skill_bundle(refs, body.bundle_name)
        proposal = skills_mod.create_approval_proposal(
            conn, subject_id=build["artifact_digest"],
            subject_digest=build["manifest_digest"], bundle_name=body.bundle_name,
            build_inputs=refs)
        conn.commit()
    finally:
        conn.close()
    return {"bundleName": body.bundle_name, "artifactDigest": build["artifact_digest"],
            "manifestDigest": build["manifest_digest"], "proposalId": proposal}


@router.post("/skills/proposals/{proposal_id}/decisions")
def record_decision_api(proposal_id: str, body: models.ApprovalDecisionRequest):
    from app.db import connect
    from app.runtime.skills import record_approval_decision, approval_quorum_reached
    conn = connect()
    try:
        decision = record_approval_decision(
            conn, proposal_id=proposal_id, approval_role=body.approval_role,
            approver_identity=body.approver_identity, approved=body.approved)
        quorum = approval_quorum_reached(conn, proposal_id)
        conn.commit()
    finally:
        conn.close()
    return {"decisionId": decision["decision_id"], "approvalRole": body.approval_role,
            "approved": body.approved, "quorumReached": quorum}


@router.post("/skills/publications:advance")
def advance_publication_api(body: models.PublicationAdvanceRequest):
    from app.db import connect
    from app.runtime.skills import publish_bundle
    # 评审 should-1：提案不存在 → 404；存在但未达 quorum/拒绝 → 409
    exists = execute_one("SELECT 1 FROM pi_approval_proposals WHERE proposal_id=%s",
                         (body.proposal_id,))
    if not exists:
        raise HTTPException(404, f"proposal {body.proposal_id} not found")
    conn = connect()
    try:
        try:
            snap = publish_bundle(conn, proposal_id=body.proposal_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        conn.commit()
    finally:
        conn.close()
    return {"snapshotId": snap["skillBundleSnapshotId"], "state": "ACTIVE",
            "bundleName": snap["bundleName"]}


@router.get("/skills/bundles", response_model=list[models.SkillBundleOut])
def list_skill_bundles():
    rows = execute(
        "SELECT snapshot_id, bundle_name, bundle_revision, approval_proposal_id, "
        "artifact_digest, manifest_digest, created_at FROM pi_skill_bundle_snapshots "
        "ORDER BY created_at DESC",
    )
    return [models.SkillBundleOut(**r) for r in rows]


@router.get("/skills/proposals", response_model=list[models.ProposalOut])
def list_skill_proposals():
    rows = execute(
        "SELECT proposal_id, bundle_name, status, subject_digest, created_at "
        "FROM pi_approval_proposals ORDER BY created_at DESC",
    )
    return [models.ProposalOut(**r) for r in rows]


@router.get("/skills/publication", response_model=models.PublicationOut)
def get_publication():
    from app.runtime.skills import active_bundle_summary
    conn = connect()
    try:
        packages = active_bundle_summary(conn)
    finally:
        conn.close()
    row = execute_one(
        "SELECT env_scope, snapshot_id, state, row_version "
        "FROM pi_skill_publication_pointers WHERE env_scope='local'")
    if row is None:
        return models.PublicationOut(env_scope="local", snapshot_id=None,
                                     state="NONE", row_version=0)
    return models.PublicationOut(**row, packages=packages or [])


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