"""测试隔离：每用例前后清空任务表（级联删 attempts/events），避免共享 PG 状态污染。"""
import pytest

from app.db import execute


@pytest.fixture(autouse=True)
def clean_tasks_before_each():
    execute("DELETE FROM pi_tasks")
    yield
    execute("DELETE FROM pi_tasks")