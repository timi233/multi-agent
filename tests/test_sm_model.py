"""状态机模型检查（对照手册 §13.1 SM-01/SM-02/SM-03/SM-08 精神）。

把 pi_tasks / pi_attempts 的转移白名单翻译为可执行模型并穷举检查：
- SM-01 白名单外迁移必被拒绝（Task 用真实 assert_transition 穷举全状态对）
- SM-02 终态闭合：终态出度=0
- SM-03 每个可达非终态存在 StateDeadlinePolicy 出口且出口 ∈ 白名单
- 死枚举审计：迁移注释声明 vs 实际可达（Attempt 的 RUNNING/FAILED）
"""
import pytest

from scripts.sm_model import (
    ATTEMPT_SM,
    DEADLINE_POLICY,
    TASK_SM,
    all_ok,
    audit_dead_enum,
    check_sm01_whitelist,
    check_sm02_terminal_closed,
    check_sm03_deadline,
    run_all,
)
from app.control.lifecycle import InvalidTransition, assert_transition

MACHINES = [TASK_SM, ATTEMPT_SM]


@pytest.mark.parametrize("machine", MACHINES, ids=lambda m: m.name)
def test_sm01_whitelist_exhaustive(machine):
    """SM-01：穷举全状态对，白名单内放行、白名单外拒绝。"""
    r = check_sm01_whitelist(machine, use_real=True)
    assert r.ok, r.findings


def test_sm01_task_real_matrix():
    """Task 真实 assert_transition 与白名单模型完全一致（25 对全查）。"""
    for old in TASK_SM.states:
        for new in TASK_SM.states:
            expected = new in TASK_SM.transitions[old]
            try:
                assert_transition(old, new)
                actual = True
            except InvalidTransition:
                actual = False
            assert actual == expected, f"{old} -> {new}"


def test_sm01_illegal_transitions_rejected():
    """代表性非法迁移必须抛 INVALID_STATE_TRANSITION（SM-01）。"""
    for old, new in [("QUEUED", "SUCCESS"), ("RUNNING", "QUEUED"),
                     ("SUCCESS", "RUNNING"), ("CANCELLED", "FAILED")]:
        with pytest.raises(InvalidTransition):
            assert_transition(old, new)


@pytest.mark.parametrize("machine", MACHINES, ids=lambda m: m.name)
def test_sm02_terminal_closed(machine):
    """SM-02：终态出度必须为 0（终态后写入被拒绝）。"""
    r = check_sm02_terminal_closed(machine)
    assert r.ok, r.findings
    for st in machine.terminal:
        assert machine.transitions[st] == ()


@pytest.mark.parametrize("machine", MACHINES, ids=lambda m: m.name)
def test_sm03_deadline_policy(machine):
    """SM-03：每个可达非终态存在 StateDeadlinePolicy 出口且在白名单内。"""
    r = check_sm03_deadline(machine, DEADLINE_POLICY[machine.name])
    assert r.ok, r.findings


def test_sm03_deadline_outlets_are_terminal_or_restart():
    """出口目标收敛性：Task QUEUED 出口 CANCELLED（取消）；RUNNING 出口 FAILED（恢复收敛）。"""
    assert DEADLINE_POLICY["Task"]["QUEUED"] == ("CANCELLED",)
    assert DEADLINE_POLICY["Task"]["RUNNING"] == ("FAILED",)
    assert DEADLINE_POLICY["Attempt"]["CLAIMED"] == ("TERMINAL_REPORTED",)


def test_dead_enum_audit_attempt():
    """Attempt 声明枚举 RUNNING/FAILED 为死枚举（实现审计发现，基线固化）。"""
    r = audit_dead_enum(ATTEMPT_SM)
    assert r.ok
    assert any("RUNNING" in f and "FAILED" in f for f in r.findings)


def test_run_all_ok():
    """全套模型检查必须全绿（SM-01/02/03 + SM-08 事件绑定 + 死枚举审计）。"""
    results = run_all()
    assert all_ok(results)
    # 断言检查数量：Task 5 项（SM01/02/03/SM08/枚举审计），Attempt 4 项
    assert len(results["Task"]) == 5 and len(results["Attempt"]) == 4