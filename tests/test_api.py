"""控制面 API 集成测试（TestClient，不启动真实 worker）。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

REMOTE = "http://testserver"


@pytest.fixture()
def client():
    app = create_app(enable_worker=False)
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_and_get_task(client):
    r = client.post("/api/v1/tasks", json={
        "title": "测试任务", "prompt": "在工作区写一个 hi.txt 内容为 hi",
    })
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "QUEUED" and t["workspace"].startswith("task-")
    tid = t["id"]

    got = client.get(f"/api/v1/tasks/{tid}")
    assert got.status_code == 200 and got.json()["id"] == tid

    evs = client.get(f"/api/v1/tasks/{tid}/events")
    assert evs.status_code == 200
    types = [e["event_type"] for e in evs.json()]
    assert "TASK_CREATED" in types and types[0] == "TASK_CREATED"


def test_list_tasks_pagination(client):
    for i in range(3):
        client.post("/api/v1/tasks", json={"title": f"t{i}", "prompt": "p"})
    rows = client.get("/api/v1/tasks?limit=2").json()
    assert len(rows) == 2


def test_cancel_queued_task(client):
    tid = client.post("/api/v1/tasks", json={"title": "x", "prompt": "p"}).json()["id"]
    r = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    # 终态不可再取消
    r2 = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert r2.status_code in (409, 400)


def test_cancel_unknown_task(client):
    assert client.post(f"/api/v1/tasks/{uuid.uuid4().hex}/cancel").status_code == 404


def test_workspace_listing_and_file(client):
    tid = client.post("/api/v1/tasks", json={"title": "ws", "prompt": "p"}).json()["id"]
    ws = client.get(f"/api/v1/tasks/{tid}/workspace")
    assert ws.status_code == 200 and ws.json() == []
    # 在工作区写入文件后可见
    root = __import__("pathlib").Path("workspaces") / f"task-{tid}"
    root.mkdir(parents=True, exist_ok=True)
    (root / "hello.txt").write_text("hi")
    try:
        entries = client.get(f"/api/v1/tasks/{tid}/workspace").json()
        assert any(e["path"] == "hello.txt" and e["kind"] == "file" for e in entries)
        fc = client.get(f"/api/v1/tasks/{tid}/workspace/file?path=hello.txt")
        assert fc.status_code == 200 and fc.json()["content"] == "hi"
        # 逃逸路径拒绝
        assert client.get(f"/api/v1/tasks/{tid}/workspace/file?path=../x").status_code == 400
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)