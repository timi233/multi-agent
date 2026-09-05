"""G3 Artifact/CAS/Evidence：cas put/get/verify、snapshot_workspace、
ingest_step_evidence、worker 集成（成功/失败/预算路径）、API 端点。"""
import json

import pytest

from app.db import connect, execute, execute_one
from app.runtime import cas
from app.runtime.cas import CasError, blob_path, get, put_bytes, put_file, verify_digest
from app.runtime.evidence import (
    ingest_step_evidence,
    snapshot_workspace,
    verify_envelope_integrity,
)
from app.runtime.terminal import verified_terminal_envelope
from pathlib import Path

TID = "0123456789abcdef"


def test_cas_put_get_dedup_and_verify():
    import hashlib

    data = b"hello artifact"
    d1 = put_bytes(data)
    d2 = put_bytes(data)  # 幂等去重
    assert d1 == d2 == "sha256:" + hashlib.sha256(data).hexdigest()
    assert get(d1) == data
    assert verify_digest(d1) is True
    blob = blob_path(d1)
    assert blob.is_file()
    # 篡改检测（写回恢复，避免污染持久化 blob 影响后续用例）
    blob.write_bytes(b"corrupted")
    assert verify_digest(d1) is False
    blob.write_bytes(data)
    assert verify_digest(d1) is True
    # 评审 should-fix：已存在但同尺寸损坏不得被误判为幂等成功
    tampered = b"HELLO ARTIFACT"  # 同长度不同内容
    blob.write_bytes(tampered)
    with pytest.raises(CasError, match="内容不匹配"):
        put_bytes(data)
    blob.write_bytes(data)  # 恢复
    assert put_bytes(data) == d1
    # 非法 digest 一律拒绝（路径注入防护）
    with pytest.raises(CasError):
        get("sha256:" + "z" * 64)
    with pytest.raises(CasError):
        blob_path("../../etc/passwd")


def test_cas_put_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"data")
    d = put_file(f)
    assert verify_digest(d) is True


def test_snapshot_workspace_limits(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("aaa")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.log").write_text("bbbb")
    artifacts, missing = snapshot_workspace(ws)
    paths = [a["path"] for a in artifacts]
    assert paths == ["a.txt", "sub/b.log"]  # 相对路径、排序
    assert all(a["digest"].startswith("sha256:") for a in artifacts)
    assert missing == []
    # 超大文件：too-large 披露且不入清单
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    from app.config import settings
    (ws2 / "big.bin").write_bytes(b"x" * (settings.max_artifact_bytes + 1))
    artifacts2, missing2 = snapshot_workspace(ws2)
    assert artifacts2 == []  # 唯一文件超限不入清单
    assert any("artifact-skipped" in m for m in missing2)


def test_ingest_success_envelope_verified_and_cas_linked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "out.md").write_text("done")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                        "VALUES (%s,'t','p','w','QUEUED')", (TID,))
        conn.commit()
    with connect() as conn:
        env = ingest_step_evidence(
            conn, task_id=TID, attempt_id="1010101010101010",
            run_id="2020202020202020", step_index=1, workspace_dir=ws,
            outcome_class="SUCCESS_COMPLETE", status="SUCCESS", stop_reason=None)
        assert verified_terminal_envelope(env) == []
        assert env["signature"]["issuer"] == "pi.node"
        conn.commit()
        assert verify_envelope_integrity(conn, env["terminalEnvelopeId"]) is True
    arts = execute("SELECT path, digest, size FROM pi_artifacts WHERE task_id=%s",
                   (TID,))
    assert [a["path"] for a in arts] == ["out.md"]
    assert all(verify_digest(a["digest"]) for a in arts)
    row = execute_one(
        "SELECT envelope_id, verified_ok FROM pi_terminal_envelopes WHERE task_id=%s",
        (TID,))
    assert row["envelope_id"] == env["terminalEnvelopeId"]
    assert row["verified_ok"] is True


def test_ingest_failure_envelope_allows_missing_evidence(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                        "VALUES (%s,'t','p','w','QUEUED')", (TID,))
        conn.commit()
    with connect() as conn:
        env = ingest_step_evidence(
            conn, task_id=TID, attempt_id="1010101010101010",
            run_id="2020202020202020", step_index=1, workspace_dir=ws,
            outcome_class="FAILURE_PLATFORM_PROOF", status="BUDGET_EXHAUSTED",
            stop_reason="quota")
        assert verified_terminal_envelope(env) == []
        assert "missingEvidenceReasons" in env["runtimeObserved"]
        conn.commit()


def _insert_task(prompt="hello") -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                "VALUES (%s,'t',%s,'w','RUNNING') RETURNING *",
                (TID, prompt))
            return cur.fetchone()


def _run_task(task: dict, fake_run_attempt):
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


def _workspace_for(task) -> None:
    from pathlib import Path
    from app.config import settings
    p = Path(settings.workspaces_dir) / task["workspace"]
    p.mkdir(parents=True, exist_ok=True)
    (p / "result.txt").write_text("made by agent")
    return p


def _fake_ok(task, **kw):
    return True, "done", None


def test_worker_success_ingests_evidence_and_artifacts():
    task = _insert_task()
    ws = _workspace_for(task)
    (ws / "result.txt").write_text("made by agent")
    _run_task(task, _fake_ok)
    assert execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))["status"] == "SUCCESS"
    row = execute_one(
        "SELECT envelope_id, outcome_class, status, verified_ok "
        "FROM pi_terminal_envelopes WHERE task_id=%s", (TID,))
    assert row["outcome_class"] == "SUCCESS_COMPLETE"
    assert row["verified_ok"] is True
    arts = execute("SELECT path FROM pi_artifacts WHERE task_id=%s ORDER BY path", (TID,))
    assert [a["path"] for a in arts] == ["result.txt"]


def test_worker_business_failure_ingests_failure_envelope():
    task = _insert_task()
    _workspace_for(task)

    def fake(task, **kw):
        return False, "", "tool denied"

    _run_task(task, fake)
    row = execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "FAILED"
    env = execute_one(
        "SELECT outcome_class, status FROM pi_terminal_envelopes WHERE task_id=%s", (TID,))
    assert env["outcome_class"] == "FAILURE_PLATFORM_PROOF"
    assert env["status"] == "FAILED"


def test_worker_budget_path_ingests_evidence_isolated():
    from app.runtime.budget import BudgetExceeded
    task = _insert_task()
    _workspace_for(task)

    def fake(task, **kw):
        raise BudgetExceeded(needed=100, available=9)

    _run_task(task, fake)
    row = execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "FAILED"
    env = execute_one(
        "SELECT outcome_class, status FROM pi_terminal_envelopes WHERE task_id=%s", (TID,))
    assert env["outcome_class"] == "FAILURE_PLATFORM_PROOF"
    assert env["status"] == "BUDGET_EXHAUSTED"


def test_api_artifacts_and_envelopes_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    task = _insert_task()
    ws = _workspace_for(task)
    _run_task(task, _fake_ok)
    client = TestClient(app)
    r = client.get(f"/api/v1/tasks/{TID}/artifacts")
    assert r.status_code == 200
    data = r.json()
    assert len(data) >= 1
    assert {"path", "digest", "size", "kind"} <= set(data[0])
    r2 = client.get(f"/api/v1/tasks/{TID}/terminal-envelopes")
    assert r2.status_code == 200
    assert r2.json()[0]["outcome_class"] == "SUCCESS_COMPLETE"
    r3 = client.get("/api/v1/tasks/ffffffffffffffff/artifacts")
    assert r3.status_code == 404


def test_snapshot_skips_symlink_and_not_regular(tmp_path):
    """评审 block-3：symlink/非普通文件不收入（外部文件不得进 CAS）。"""
    outside = tmp_path / "secret.txt"
    outside.write_text("TOKEN=x")
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / "good.txt").write_text("ok")
    (ws / "link.txt").symlink_to(outside)
    artifacts, missing = snapshot_workspace(ws)
    paths = [a["path"] for a in artifacts]
    assert paths == ["good.txt"]
    assert any("link.txt" in m and "artifact-skipped" in m for m in missing)

    # 目录 symlink：不静默漏报（评审 block）
    outside_dir = tmp_path / "secret_dir"
    outside_dir.mkdir()
    (outside_dir / "inner.txt").write_text("SECRET")
    ws3 = tmp_path / "ws3"
    ws3.mkdir()
    (ws3 / "dirlink").symlink_to(outside_dir, target_is_directory=True)
    # 目录 symlink 不进 dirnames 遍历，也不进 filenames——但必须出现在 missing
    artifacts3, missing3 = snapshot_workspace(ws3)
    assert artifacts3 == []
    assert any("dirlink" in m and "dir-symlink" in m for m in missing3)


def test_ingest_rejects_bad_signature(tmp_path, monkeypatch):
    """评审 block-1：签名无效的信封在归档边界必须被拒（非仅 helper 层面）。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_text("x")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                        "VALUES (%s,'t','p','w','QUEUED')", (TID,))
        conn.commit()

    from app.runtime import evidence as ev_mod
    from app.runtime.terminal import build_terminal_envelope as orig_build
    from app.runtime.terminal import verify_terminal_signature

    def bad_build(**kw):
        env = orig_build(**kw)
        # 合法 base64 形态的伪造签名值（过 Schema pattern，在验签层被拒）
        env["signature"]["value"] = "A" * 86 + "=="
        env["signature"]["issuer"] = "attacker"
        return env

    monkeypatch.setattr(ev_mod, "build_terminal_envelope", bad_build)
    with pytest.raises(ValueError, match="验签失败"):
        with connect() as conn:
            ingest_step_evidence(
                conn, task_id=TID, attempt_id="1010101010101010",
                run_id="2020202020202020", step_index=1, workspace_dir=ws,
                outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                stop_reason=None)
            conn.rollback()


def test_integrity_detects_db_signature_tamper():
    """评审 block-1：归档后在 DB 中篡改 signature.value，完整性复核必须 False。"""
    ws = Path(__import__("tempfile").mkdtemp())
    ws.mkdir(exist_ok=True)
    (ws / "a.txt").write_text("x")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                        "VALUES (%s,'t','p','w','QUEUED')", (TID,))
        conn.commit()
    with connect() as conn:
        env = ingest_step_evidence(
            conn, task_id=TID, attempt_id="1010101010101010",
            run_id="2020202020202020", step_index=1, workspace_dir=ws,
            outcome_class="SUCCESS_COMPLETE", status="SUCCESS", stop_reason=None)
        assert verify_envelope_integrity(conn, env["terminalEnvelopeId"]) is True
        with conn.cursor() as cur:
            cur.execute("SELECT envelope FROM pi_terminal_envelopes "
                        "WHERE envelope_id=%s", (env["terminalEnvelopeId"],))
            row = cur.fetchone()
        tampered = dict(row["envelope"])
        tampered["signature"] = dict(row["envelope"]["signature"], value="x" * 86)
        conn.execute("UPDATE pi_terminal_envelopes SET envelope=%s::jsonb "
                     "WHERE envelope_id=%s",
                     (json.dumps(tampered), env["terminalEnvelopeId"]))
        conn.commit()
    with connect() as conn:
        assert verify_envelope_integrity(conn, env["terminalEnvelopeId"]) is False


def test_ingest_success_blocks_when_evidence_missing(tmp_path):
    """评审 should-fix-2：SUCCESS 且证据缺失（超限）→ 阻止完整成功。"""
    from app.config import settings
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.bin").write_bytes(b"x" * (settings.max_artifact_bytes + 1))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
                        "VALUES (%s,'t','p','w','QUEUED')", (TID,))
        conn.commit()
    from app.runtime.evidence import ingest_step_evidence
    with pytest.raises(ValueError, match="缺失证据"):
        with connect() as conn:
            ingest_step_evidence(
                conn, task_id=TID, attempt_id="1010101010101010",
                run_id="2020202020202020", step_index=1, workspace_dir=ws,
                outcome_class="SUCCESS_COMPLETE", status="SUCCESS",
                stop_reason=None)
            conn.rollback()


def test_cancel_race_envelope_is_cancelled_confirmed():
    """评审 block-4：precheck 后步骤执行期间发生取消（worker 预算异常路径），
    收存先读真实 Task 终态→信封 CANCELLED_CONFIRMED；任务不被覆盖为 FAILED。"""
    from app.runtime.budget import BudgetExceeded

    task = _insert_task()
    _workspace_for(task)
    cancelled = {"done": False}

    def fake(task, **kw):
        if not cancelled["done"]:  # 步骤已 EXECUTING 时触发取消（执行期间）
            cancelled["done"] = True
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE pi_tasks SET status='CANCELLED', "
                                "finished_at=now() WHERE id=%s", (TID,))
                conn.commit()
        raise BudgetExceeded(needed=100, available=9)

    _run_task(task, fake)
    row = execute_one("SELECT status FROM pi_tasks WHERE id=%s", (TID,))
    assert row["status"] == "CANCELLED"  # 不被 worker 覆盖为 FAILED
    env = execute_one(
        "SELECT outcome_class, status FROM pi_terminal_envelopes WHERE task_id=%s", (TID,))
    assert env is not None, "执行期间取消后必须仍有终态信封"
    assert env["outcome_class"] == "CANCELLED_CONFIRMED"
    assert env["status"] == "CANCELLED"


def test_cas_blob_stored_under_data_dir():
    """CAS 落 data/cas（gitignore 排除 data/）：blob 路径由 digest 派生。"""
    d = put_bytes(b"persist-me")
    assert "data/cas" in str(blob_path(d))
    assert blob_path(d).is_file()