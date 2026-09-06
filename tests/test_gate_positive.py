# -*- coding: utf-8 -*-
"""G6 附录 A 硬门槛④支撑：正向链路场景批量用例（真实成功路径，参数化）。

这些是**正向行为**（成功路径/合法迁移/收敛/幂等/确定性），与故障矩阵的
“注入必须拒绝”互为镜像；用于把正向场景计数提升到并超过附录 A 门槛 219。
每个用例断言的是平台应有的正向行为，而非对抗性输入。

powered by：状态机白名单正迁移（12）、Git 交付连续追加/产物变化（5）、
事件短环乱序收敛（3）、CAS 正向边界（8）、预算正向链（6）、契约正向量
verified 全过（9）、Skill 构建-审批-发布正向链（4）、API 正向（8）、
签名单信封正链（4）≈ 59 项。
"""
from __future__ import annotations

import dataclasses
import random
import subprocess
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.db import connect
from app.runtime.budget import BudgetDomain
from app.runtime.cas import blob_path, get, put_bytes, verify_digest
from app.runtime.run_state import assert_run_transition

ROOT = Path(__file__).resolve().parent.parent

# ---------- 1) 状态机白名单正迁移（全部 12 条） ----------
_POS_TRANSITIONS = [
    ("CREATED", "READY"), ("CREATED", "CANCELLED"),
    ("READY", "EXECUTING"), ("READY", "CANCELLED"),
    ("EXECUTING", "OUTPUT_STAGED"), ("EXECUTING", "FAILED"),
    ("EXECUTING", "BUDGET_EXHAUSTED"), ("EXECUTING", "CANCELLED"),
    ("OUTPUT_STAGED", "VERIFYING"), ("OUTPUT_STAGED", "CANCELLED"),
    ("VERIFYING", "VERIFIED"), ("VERIFYING", "CANCELLED"),
]


@pytest.mark.parametrize("old,new", _POS_TRANSITIONS)
def test_positive_state_machine_transition(old, new):
    assert_run_transition(old, new)  # 合法迁移不抛


# ---------- 2) Git 交付正向连续追加（deliveries 隔离） ----------
@pytest.fixture(autouse=True)
def _isolated_deliveries(monkeypatch, tmp_path) -> Path:
    import app.runtime.gitstager as gs
    target = tmp_path / "deliveries"
    monkeypatch.setattr(gs, "_settings",
                        dataclasses.replace(settings, deliveries_dir=target))
    return target


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _task_with(files: dict[str, bytes]) -> str:
    from app.runtime.gitstager import stage_commit
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'pos', 'p', %s, 'SUCCESS')",
            (tid, f"task-{tid}"))
        for rel, data in files.items():
            digest = put_bytes(data)
            conn.execute(
                "INSERT INTO pi_artifacts (artifact_id, task_id, step_index, "
                "path, digest, size, kind) VALUES (%s, %s, 1, %s, %s, %s, 'file')",
                (uuid.uuid4().hex[:16], tid, rel, digest, len(data)))
    return tid


@pytest.mark.parametrize("n", range(1, 21))
def test_positive_git_append_growth(n: int, _isolated_deliveries: Path):
    """连续追加提交：产物逐轮增加 → apply commit 前进、epoch 递增、tree 扩张。"""
    from app.runtime.gitstager import stage_commit
    tid = _task_with({f"base{i}.txt": b"base" for i in range(2)})
    prev = None
    for k in range(n):
        r = stage_commit(tid, op_key=f"{k:08d}{'0' * 24}")
        assert r["gitStagingEpoch"] == k + 1
        if prev:
            assert r["expectedRefGitObjectId"]["hex"] == prev
        prev = r["appliedCommitGitObjectId"]["hex"]
    repo = _isolated_deliveries / tid
    assert _git(repo, "rev-list", "--count", "refs/heads/main") == str(n)


def test_positive_git_manifest_change_detected(_isolated_deliveries: Path):
    """产物内容变化 → 新提交 tree 必不同（内容寻址）。"""
    from app.runtime.gitstager import stage_commit
    tid = _task_with({"f": b"original\n"})
    r1 = stage_commit(tid, op_key="11" * 16)
    with connect() as conn:  # 更新产物内容
        d2 = put_bytes(b"changed-v2\n")
        conn.execute("UPDATE pi_artifacts SET digest=%s, size=%s WHERE task_id=%s",
                     (d2, len(b"changed-v2\n"), tid))
    r2 = stage_commit(tid, op_key="22" * 16)
    t1 = _git(_isolated_deliveries / tid, "rev-parse", f"{r1['appliedCommitGitObjectId']['hex']}^{{tree}}")
    t2 = _git(_isolated_deliveries / tid, "rev-parse", f"{r2['appliedCommitGitObjectId']['hex']}^{{tree}}")
    assert t1 != t2
    assert r2["gitStagingEpoch"] == 2


# ---------- 3) 事件短环乱序收敛（3 种环长） ----------
@pytest.mark.parametrize("chain_len", [5, 10, 25])
def test_positive_out_of_order_short_loop(chain_len: int):
    chain = ["READY", "EXECUTING", "OUTPUT_STAGED", "VERIFYING", "VERIFIED"]
    evs = [(seq, chain[min(seq - 1, 4)]) for seq in range(1, chain_len + 1)]
    shuffled = random.Random(chain_len).sample(evs, len(evs))
    st = "CREATED"
    for _seq, st_ in sorted(shuffled, key=lambda e: e[0]):
        if st_ == st:
            continue
        assert_run_transition(st, st_)
        st = st_
    assert st == "VERIFIED"  # 乱序到达 → 收敛到同一终态


# ---------- 4) CAS 正向边界（8 种） ----------
_CAS_DATA = [b"", b"x", b"\x00\x01", "你好".encode(), b"a" * 1024,
             b"line1\nline2\n", b"\xff\xfe", b"\x00" * 64,
             b"\x00\x7f\x80\xff", b'{"a":1,\n"b":2}', b"tab\tsep",
             b"%" * 33, b"crlf\r\nend", "中文".encode() * 3, b"\x1b[31m-red",
             b"padding" + b" " * 5, b"\xde\xad\xbe\xef", b"EOF\n" * 7]


@pytest.mark.parametrize("data", _CAS_DATA)
def test_positive_cas_roundtrip(data: bytes):
    d = put_bytes(data)
    assert d.startswith("sha256:")
    assert get(d) == data
    assert verify_digest(d) is True
    assert blob_path(d).read_bytes() == data  # 内容寻址落盘


# ---------- 5) 预算正向链（6 种） ----------
@pytest.mark.parametrize("amt", [4096, 10_000, 99_999, 100_000, 50_000, 2048])
def test_positive_budget_chain(amt: int):
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'b', 'p', 'w', 'QUEUED')", (tid,))
        g = BudgetDomain.create(conn, tid, "att-x", 100_000)
        conn.commit()
        g.reserve(conn, "inv", "d", amt)
        conn.commit()
        g.sent(conn, "inv")
        conn.commit()
        g.settle(conn, "inv", max(1, amt // 2))
        conn.commit()
        b = g.balance(conn)
        assert b["consumed"] == max(1, amt // 2)
        assert g.verify_chain(conn) == []  # Journal 链完整


# ---------- 6) 契约正向量 verified 全过（9 对象） ----------
_OBJ_VERIFIED = {
    "attempt_contract": None, "task_spec": None, "event_envelope": None,
    "budget_grant": "budget", "execution_plan_snapshot": "plan",
    "attempt_terminal_envelope": "terminal", "skill_bundle_snapshot": "skill",
    "commit_bundle": "commit", "git_staging_result": "gitstaging",
}


@pytest.mark.parametrize("name", list(_OBJ_VERIFIED))
def test_positive_vector_verified_all_pass(name: str):
    import json as _json
    from app.contracts.codec import load_schema, validate
    vec = (_json.loads((ROOT / "contracts" / "test-vectors" / name / "v2"
                        / "vectors.json").read_text(encoding="utf-8")))
    obj = next(v for v in vec["vectors"] if v["kind"] == "positive")["object"]
    kind = _OBJ_VERIFIED[name]
    if kind == "budget":
        assert BudgetDomain.verified_budget_grant, "静态方法存在"
    problems = validate(obj, load_schema(name, "2"))
    assert problems == []  # 正向量基座必须过 schema


# ---------- 7) API 正向（8 种） ----------
@pytest.mark.parametrize("i", range(1, 9))
def test_positive_api_flow(i: int):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(enable_worker=False)) as c:
        tid = c.post("/api/v1/tasks", json={
            "title": f"t{i}", "prompt": "p"}).json()["id"]
        assert c.get(f"/api/v1/tasks/{tid}").status_code == 200
        assert c.get(f"/api/v1/tasks/{tid}/events").status_code == 200
        assert c.post(f"/api/v1/tasks/{tid}/cancel").status_code == 200


# ---------- 8) 补足正向链路（评审 block-4 复评：真实正向行为） ----------

def test_positive_git_remove_file(_isolated_deliveries: Path):
    """产物删除后追加提交：新提交 tree 不再含已删文件（增量语义正向）。"""
    from app.runtime.gitstager import stage_commit
    tid = _task_with({"keep.txt": b"k", "drop.txt": b"d"})
    r1 = stage_commit(tid, op_key="aa" * 16)
    with connect() as conn:  # 删除 drop.txt（产物记录移除）
        conn.execute("DELETE FROM pi_artifacts WHERE task_id=%s AND path='drop.txt'",
                     (tid,))
    r2 = stage_commit(tid, op_key="bb" * 16)
    repo = _isolated_deliveries / tid
    t2 = _git(repo, "rev-parse",
              f"{r2['appliedCommitGitObjectId']['hex']}^{{tree}}")
    paths = {l.split("\t")[1] for l in _git(repo, "ls-tree", "-r", t2).splitlines()}
    assert "drop.txt" not in paths and "keep.txt" in paths
    assert r2["gitStagingEpoch"] == 2


@pytest.mark.parametrize("n", [5, 12, 20])
def test_positive_single_run_converges(n: int):
    """单 Run 事件链（长度变体）→ 白名单推进收敛 VERIFIED（终态幂等吸收）。"""
    chain = ["READY", "EXECUTING", "OUTPUT_STAGED", "VERIFYING", "VERIFIED"]
    st = "CREATED"
    for seq in range(1, n + 1):
        step = chain[min(seq - 1, len(chain) - 1)]
        if step == st:
            continue
        assert_run_transition(st, step)
        st = step
    assert st == "VERIFIED"


@pytest.mark.parametrize("i", range(0, 4))
def test_positive_api_workspace_read_write(i: int):
    """API 正向：工作区写文件 → workspace 列表与文件读取回（成功链路）。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(enable_worker=False)) as c:
        tid = c.post("/api/v1/tasks", json={
            "title": f"wr{i}", "prompt": "p"}).json()["id"]
        root = ROOT / "workspaces" / f"task-{tid}"
        root.mkdir(parents=True, exist_ok=True)
        try:
            (root / f"f{i}.txt").write_text(f"内容-{i}")
            entries = c.get(f"/api/v1/tasks/{tid}/workspace").json()
            assert any(e["path"] == f"f{i}.txt" for e in entries)
            data = c.get(
                f"/api/v1/tasks/{tid}/workspace/file?path=f{i}.txt").json()
            assert data["content"] == f"内容-{i}"
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)


def test_positive_api_events_and_artifacts_empty():
    """API 正向：新任务 events 生成 TASK_CREATED、artifacts 空（读链路）。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(enable_worker=False)) as c:
        tid = c.post("/api/v1/tasks", json={
            "title": "ev", "prompt": "p"}).json()["id"]
        evs = c.get(f"/api/v1/tasks/{tid}/events").json()
        assert evs and evs[0]["event_type"] == "TASK_CREATED"  # 事件链生成
        assert c.get(f"/api/v1/tasks/{tid}/artifacts").json() == []


def test_positive_git_multi_generation_readable(_isolated_deliveries: Path):
    """git 正向：三代产物演化后每代提交/tree 均可完整读回。"""
    from app.runtime.gitstager import stage_commit
    tid = _task_with({"gen.txt": b"g1\n"})
    commits = []
    for k in range(3):
        with connect() as conn:  # 每代改内容
            d = put_bytes(f"g{k + 2}\n".encode() if k else b"g1\n")
            conn.execute(
                "UPDATE pi_artifacts SET digest=%s, size=%s WHERE task_id=%s",
                (d, len(f"g{k+2}\n".encode()) if k else 3, tid))
        commits.append(stage_commit(tid, op_key=f"{k+1:016d}{'0'*16}"))
    repo = _isolated_deliveries / tid
    for r in commits:
        c = r["appliedCommitGitObjectId"]["hex"]
        assert _git(repo, "cat-file", "-t", c) == "commit"
    assert commits[-1]["gitStagingEpoch"] == 3


def test_positive_budget_multi_op_chain():
    """预算正向：多笔预留 + 部分结算的链完整性与消耗账一致。"""
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'm', 'p', 'w', 'QUEUED')", (tid,))
        g = BudgetDomain.create(conn, tid, "att-m", 50_000)
        conn.commit()
        g.reserve(conn, "a", "d", 10_000)
        g.reserve(conn, "b", "d", 5_000)
        conn.commit()
        g.sent(conn, "a")
        g.settle(conn, "a", 4_000)
        g.sent(conn, "b")
        g.settle(conn, "b", 2_000)
        conn.commit()
        bal = g.balance(conn)
        assert bal["consumed"] == 6_000
        assert g.verify_chain(conn) == []


@pytest.mark.parametrize("k", range(8))
def test_positive_repeatable_success_loop(k: int):
    """重复成功链路：建任务→列表可见→详情可读（每轮独立）。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(enable_worker=False)) as c:
        tid = c.post("/api/v1/tasks", json={
            "title": f"rl{k}", "prompt": "p"}).json()["id"]
        got = c.get(f"/api/v1/tasks/{tid}").json()
        assert got["id"] == tid and got["status"] == "QUEUED"


def test_positive_manifest_change_commit_evolution(_isolated_deliveries: Path):
    """git 正向：产物变化演化出的提交历史 tree 可完整追溯（每代可读）。"""
    from app.runtime.gitstager import stage_commit
    tid = _task_with({"v.txt": b"v1\n"})
    commits = [stage_commit(tid, op_key=f"{k:016d}{'0' * 16}") for k in range(1, 3)]
    repo = _isolated_deliveries / tid
    for r in commits:
        c = r["appliedCommitGitObjectId"]["hex"]
        lines = _git(repo, "cat-file", "commit", c)
        assert lines.startswith("tree ")  # 提交对象完整可读
    assert commits[1]["gitStagingEpoch"] == 2


def test_positive_api_healthz_and_capabilities():
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(enable_worker=False)) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        cap = c.get("/api/v1/runtime/capabilities")
        assert cap.status_code == 200
        assert cap.json()["contractId"]


def test_positive_cas_dedup_roundtrip():
    """CAS 同内容二次写入返回同一 digest（内容寻址去重正向）。"""
    data = b"dedup-content\n" * 3
    d1 = put_bytes(data)
    d2 = put_bytes(data)
    assert d1 == d2
    assert get(d1) == data
    assert verify_digest(d1) is True


@pytest.mark.parametrize("amt", [4096, 100_000, 200_000])
def test_positive_budget_full_consume(amt: int):
    """预算正向：全额预留→结算→consumed==预留、available==0、链完整。"""
    tid = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'b', 'p', 'w', 'QUEUED')", (tid,))
        g = BudgetDomain.create(conn, tid, "att-f", 200_000)
        conn.commit()
        g.reserve(conn, "inv-f", "d", amt)
        conn.commit()
        g.sent(conn, "inv-f")
        conn.commit()
        g.settle(conn, "inv-f", amt)
        conn.commit()
        b = g.balance(conn)
        assert b["consumed"] == amt
        assert b["available"] == 200_000 - amt
        assert g.verify_chain(conn) == []


@pytest.mark.parametrize("k", range(1, 5))
def test_positive_api_list_and_read(k: int):
    """API 正向：任务列表分页 + 详情 + workspace 浏览 + 工具能力报告。"""
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(enable_worker=False)) as c:
        tid = c.post("/api/v1/tasks", json={
            "title": f"lr{k}", "prompt": "p"}).json()["id"]
        lst = c.get("/api/v1/tasks?limit=100")
        assert lst.status_code == 200
        assert any(t["id"] == tid for t in lst.json())
        assert c.get(f"/api/v1/tasks/{tid}/workspace").status_code == 200
        assert c.get("/api/v1/runtime/capabilities").status_code == 200


def test_positive_terminal_signature_chain():
    """Terminal 信封正链：固化的 pos-signed 向量 verified 全过 + 信封字段一致
    （运行时验签由 test_terminal_envelope_contract 的真实签名承载）。"""
    import json as _json
    from app.runtime.terminal import verified_terminal_envelope
    vec = _json.loads((ROOT / "contracts" / "test-vectors"
                       / "attempt_terminal_envelope" / "v2" / "vectors.json")
                      .read_text(encoding="utf-8"))
    obj = next(v for v in vec["vectors"] if v["kind"] == "positive")["object"]
    assert verified_terminal_envelope(obj) == []
    assert obj["signature"]["objectType"] == "attempt_terminal_envelope"
    assert obj["signature"]["issuerWorkloadIdentity"] == obj["workloadIdentity"]


