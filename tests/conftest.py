"""测试隔离：每用例前后清空任务表（级联删 attempts/events/runs/artifacts/
envelopes），并清理测试生成的固定工作区（workspaces/w*，真实任务为
workspaces/task-* 前缀不受影响）。"""
import shutil
from pathlib import Path

import pytest

from app.db import execute

WS_ROOT = Path(__file__).resolve().parent.parent / "workspaces"


@pytest.fixture(autouse=True)
def clean_tasks_before_each():
    _clean()
    yield
    _clean()


def _clean():
    execute("DELETE FROM pi_skill_publication_pointers")
    execute("DELETE FROM pi_skill_bundle_snapshots")
    execute("DELETE FROM pi_approval_decisions")
    execute("DELETE FROM pi_approval_proposals")
    execute("DELETE FROM pi_skill_packages")
    execute("DELETE FROM pi_tasks")
    for p in WS_ROOT.glob("w*"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)