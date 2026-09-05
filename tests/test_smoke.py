"""冒烟测试：S1 PostgreSQL 可达、迁移表存在、状态机转移约束。"""
import pytest

from app.db import execute
from app.control.lifecycle import assert_transition, InvalidTransition


@pytest.fixture(scope="module")
def db_tables():
    return {r["tablename"] for r in execute(
        "select tablename from pg_tables where schemaname='public'"
    )}


def test_core_tables_exist(db_tables):
    assert {"pi_tasks", "pi_attempts", "pi_events"} <= db_tables


def test_db_clock_utc():
    row = execute("select extract(timezone from now()) as tz_sec")[0]
    tz_sec = row["tz_sec"].total_seconds() if hasattr(row["tz_sec"], "total_seconds") else float(row["tz_sec"])
    assert abs(tz_sec) < 60  # UTC 时区（分钟级容差）


def test_transition_rules():
    assert_transition("QUEUED", "RUNNING")
    assert_transition("RUNNING", "SUCCESS")
    with pytest.raises(InvalidTransition):
        assert_transition("SUCCESS", "RUNNING")
    with pytest.raises(InvalidTransition):
        assert_transition("QUEUED", "SUCCESS")