"""Pi 平台 MVP 入口：HTTP 服务（含后台 worker 线程）+ CLI。

用法：
  python -m app.main serve --port 8000     # 启动 Web+API
  python -m app.main submit "任务提示词" --title xxx
  python -m app.main list
  python -m app.main status <task_id>
"""
from __future__ import annotations

import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .control.api import router
from .db import execute
from .worker import Worker

BASE = Path(__file__).resolve().parent.parent
WEB_DIR = BASE / "app" / "web"

_worker: Worker | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _worker
    if getattr(app.state, "enable_worker", True):
        _worker = Worker()
        _worker.start()
        print(f"[pi] worker started ({settings.worker_threads} threads); "
              f"model={settings.cliproxy_model}; cliproxy={settings.cliproxy_base_url}")
    yield
    if _worker:
        _worker.stop()


def create_app(enable_worker: bool = True) -> FastAPI:
    app = FastAPI(title="Pi 平台 MVP", version="0.1.0", lifespan=lifespan)
    app.state.enable_worker = enable_worker
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()


# ---------- CLI ----------

def _cli_submit(args) -> None:
    import uuid

    task_id = uuid.uuid4().hex[:16]
    workspace = f"task-{task_id}"
    execute(
        "INSERT INTO pi_tasks (id, title, prompt, workspace, status, model) VALUES (%s,%s,%s,%s,'QUEUED',%s)",
        (task_id, args.title, args.prompt, workspace, settings.cliproxy_model),
    )
    print(task_id)


def _cli_list(args) -> None:
    rows = execute("SELECT id,title,status,created_at FROM pi_tasks ORDER BY created_at DESC LIMIT 20")
    for r in rows:
        print(f"{r['id']}  {r['status']:<10} {r['created_at']}  {r['title']}")


def _cli_status(args) -> None:
    rows = execute(
        "SELECT t.*, (SELECT count(*) FROM pi_events e WHERE e.task_id=t.id) AS events "
        "FROM pi_tasks t WHERE id=%s", (args.task_id,)
    )
    if not rows:
        print(f"task {args.task_id} not found")
        return
    r = rows[0]
    print(f"id={r['id']} status={r['status']} model={r['model']} workspace={r['workspace']}")
    print(f"events={r['events']} error={r['error']}")
    if r["finished_at"]:
        print(f"finished_at={r['finished_at']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pi", description="Pi 平台 MVP")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    p_sub = sub.add_parser("submit")
    p_sub.add_argument("prompt")
    p_sub.add_argument("--title", default="CLI task")

    p_list = sub.add_parser("list")
    p_status = sub.add_parser("status")
    p_status.add_argument("task_id")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    if args.cmd == "submit":
        _cli_submit(args)
        return 0
    if args.cmd == "list":
        _cli_list(args)
        return 0
    if args.cmd == "status":
        _cli_status(args)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())