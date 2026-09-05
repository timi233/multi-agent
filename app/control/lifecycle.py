"""任务生命周期状态机（简化自蓝图 §13.1 SM-xx 的白名单迁移精神）。"""
from __future__ import annotations

ALL_TASK_STATUSES = ("QUEUED", "RUNNING", "SUCCESS", "FAILED", "CANCELLED")

# 合法迁移白名单
_VALID: dict[str, set[str]] = {
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"SUCCESS", "FAILED", "CANCELLED"},
    "SUCCESS": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}


class InvalidTransition(Exception):
    pass


def assert_transition(old: str, new: str) -> None:
    if old not in _VALID or new not in _VALID[old]:
        raise InvalidTransition(f"INVALID_STATE_TRANSITION: {old} -> {new}")


def valid_transitions(old: str) -> set[str]:
    return set(_VALID.get(old, set()))