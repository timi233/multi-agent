"""状态机模型（简化自蓝图 §13.1 SM-xx）与模型检查器。

模型源：
- Task    ：app/control/lifecycle.py 的 ALL_TASK_STATUSES / _VALID（唯一权威白名单）
- Attempt ：migrations/001_init.sql（声明枚举）+ app/worker.py 的条件 UPDATE（实际迁移点）

检查项（对照《环境搭建与验证手册 v1.3.2》§13.1）：
- SM-01 白名单外迁移不可达（穷举全状态对）；声明枚举与实际使用差异审计
- SM-02 终态闭合（终态出度=0）；终态后写入受条件 UPDATE 守卫
- SM-03 每个非终态存在 StateDeadlinePolicy 出口，且出口 ∈ 该状态白名单
- SM-08 精神：每次转移与事件写入同事务绑定（实现审计）
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.control.lifecycle import (
    ALL_TASK_STATUSES,
    InvalidTransition,
    _VALID,
    assert_transition,
)


@dataclass(frozen=True)
class StateMachine:
    name: str
    states: tuple[str, ...]
    transitions: dict[str, tuple[str, ...]]  # 白名单（实现权威）
    terminal: tuple[str, ...]
    declared_enum: tuple[str, ...] | None = None  # 表注释声明（可能含死枚举）
    source: str = ""
    reachable: tuple[str, ...] | None = None  # 实际可达状态（default=states）


TASK_SM = StateMachine(
    name="Task",
    states=ALL_TASK_STATUSES,
    transitions={s: tuple(sorted(t)) for s, t in _VALID.items()},
    terminal=("SUCCESS", "FAILED", "CANCELLED"),
    source="app/control/lifecycle.py: ALL_TASK_STATUSES / _VALID",
)

# Attempt：worker.py 全部迁移点是条件 UPDATE ... status='TERMINAL_REPORTED'；
# 声明枚举来自 migrations/001_init.sql 表注释（含 RUNNING/FAILED，实际未使用）。
ATTEMPT_SM = StateMachine(
    name="Attempt",
    states=("CLAIMED", "RUNNING", "TERMINAL_REPORTED", "FAILED"),
    transitions={
        "CLAIMED": ("TERMINAL_REPORTED",),
        "RUNNING": (),
        "TERMINAL_REPORTED": (),
        "FAILED": (),
    },
    terminal=("TERMINAL_REPORTED", "FAILED"),
    declared_enum=("CLAIMED", "RUNNING", "TERMINAL_REPORTED", "FAILED"),
    source=("migrations/001_init.sql 注释 + app/worker.py 条件 UPDATE "
            "(45/69/109/119/192 行，均 CLAIMED->TERMINAL_REPORTED)"),
    reachable=("CLAIMED", "TERMINAL_REPORTED"),  # RUNNING/FAILED 为死枚举（下方审计）
)

# StateDeadlinePolicy：每个非终态的出口（SM-03）。
# 注：本实现出口动作真实存在（用户 CANCELLED、worker 失败收敛、启动恢复 FAILED），
# 但触发时机为调用/启动时而非常驻定时器——实现差距见报告（留待调度/Gateway 层）。
DEADLINE_POLICY = {
    "Task": {"QUEUED": ("CANCELLED",), "RUNNING": ("FAILED",)},
    "Attempt": {"CLAIMED": ("TERMINAL_REPORTED",)},
}


@dataclass
class CheckResult:
    ok: bool
    findings: list[str] = field(default_factory=list)


def check_sm01_whitelist(machine: StateMachine, use_real: bool) -> CheckResult:
    """穷举全状态对：白名单内必须放行、白名单外必须拒绝（SM-01）。

    Task 用真实 assert_transition 断言；Attempt 无独立 assert 函数，其模型
    本身即从 worker.py 条件 UPDATE 提取（source 记录行号），SM-08 再核对事件绑定。"""
    r = CheckResult(True)
    for old in machine.states:
        whitelist = machine.transitions.get(old, ())
        for new in machine.states:
            expect_ok = new in whitelist
            if use_real and machine.name == "Task":
                try:
                    assert_transition(old, new)
                    actual_ok = True
                except InvalidTransition:
                    actual_ok = False
                if actual_ok != expect_ok:
                    r.ok = False
                    r.findings.append(
                        f"SM-01 Task 实现与模型不一致: {old} -> {new} "
                        f"(模型={expect_ok}, 实现={actual_ok})")
    return r


def check_sm02_terminal_closed(machine: StateMachine) -> CheckResult:
    """终态闭合（SM-02）：终态出度必须为 0，且任何状态不得迁出终态集合。"""
    r = CheckResult(True)
    for st in machine.terminal:
        if machine.transitions.get(st):
            r.ok = False
            r.findings.append(f"SM-02 {machine.name} 终态 {st} 存在出口（非法）")
    for old, outs in machine.transitions.items():
        if old in machine.terminal and outs:
            r.ok = False
    return r


def check_sm03_deadline(machine: StateMachine, policy: dict) -> CheckResult:
    """可达非终态必有 StateDeadlinePolicy 出口，且出口 ∈ 白名单（SM-03）。"""
    r = CheckResult(True)
    reachable = machine.reachable or machine.states
    non_terminal = [s for s in reachable if s not in machine.terminal]
    missing = [s for s in non_terminal if s not in policy]
    if missing:
        r.ok = False
        r.findings.append(f"SM-03 {machine.name} 无 deadline 出口的非终态: {missing}")
    for st in non_terminal:
        for out in policy.get(st, ()):
            if out not in machine.transitions.get(st, ()):
                r.ok = False
                r.findings.append(
                    f"SM-03 {machine.name} deadline 出口 {st}->{out} 不在白名单")
    return r


def check_sm08_event_binding(machine: StateMachine, event_points: dict) -> CheckResult:
    """转移与事件写入的事务绑定（SM-08 精神；蓝图为 TransitionRecorded.v2）。

    event_points: {转移: {"event": 事件名, "sameTxn": bool, "note": ...}}
    规则：终态出口转移必须与事件同事务（fail）；非终态转移跨事务记录为差距
    （不判失败，避免假 PASS —— 评审 fix-7）。"""
    r = CheckResult(True)
    for old, outs in machine.transitions.items():
        for out in outs:
            key = f"{old}->{out}"
            spec = event_points.get(key)
            if spec is None:
                r.ok = False
                r.findings.append(f"SM-08 {machine.name} 转移 {key} 无事件绑定说明")
                continue
            if out in machine.terminal and not spec["sameTxn"]:
                r.ok = False
                r.findings.append(
                    f"SM-08 {machine.name} 终态转移 {key} 事件({spec['event']})必须同事务")
            elif not spec["sameTxn"]:
                r.findings.append(
                    f"SM-08 {machine.name} 差距: {key} 事件({spec['event']})跨事务——{spec.get('note', '')}")
    return r


def audit_dead_enum(machine: StateMachine) -> CheckResult:
    """声明枚举 vs 实际可达：死枚举（声明但从未使用）差异审计（SM-01 配套）。"""
    if not machine.declared_enum:
        return CheckResult(True, ["（无声明枚举，跳过审计）"])
    reachable = machine.reachable or machine.states
    dead = [s for s in machine.declared_enum if s not in reachable]
    return CheckResult(True, ["死枚举: " + ", ".join(dead)] if dead else [])


def run_all() -> dict:
    """对 Task/Attempt 执行全部检查，返回 {机器名: CheckResult}。"""
    results: dict[str, list] = {}
    for machine in (TASK_SM, ATTEMPT_SM):
        policy = DEADLINE_POLICY[machine.name]
        checks = [
            check_sm01_whitelist(machine, use_real=True),
            check_sm02_terminal_closed(machine),
            check_sm03_deadline(machine, policy),
        ]
        results[machine.name] = checks
    # SM-08：各转移的事件绑定（worker.py / control/api.py 实现核对）
    # 注：QUEUED->RUNNING 领取事务只改任务状态；attempt 创建与 ATTEMPT_STARTED
    # 在 _run_task 的下一提交（worker.py:153-160），跨事务——推进按蓝图对齐需合并或补转发表
    task_events = {
        "QUEUED->RUNNING": {
            "event": "ATTEMPT_STARTED", "sameTxn": False,
            "note": "领取事务不写事件；attempt/事件在后续初始化事务（worker._run_task 153-160）"},
        "QUEUED->CANCELLED": {"event": "TASK_CANCELLED", "sameTxn": True},
        "RUNNING->SUCCESS": {"event": "ATTEMPT_FINISHED", "sameTxn": True},
        "RUNNING->FAILED": {"event": "ATTEMPT_FAILED", "sameTxn": True},
        "RUNNING->CANCELLED": {"event": "TASK_CANCELLED", "sameTxn": True},
    }
    results["Task"] += [check_sm08_event_binding(TASK_SM, task_events)]
    results["Task"] += [audit_dead_enum(TASK_SM)]
    attempt_events = {
        "CLAIMED->TERMINAL_REPORTED": {
            "event": "ATTEMPT_FINISHED/FAILED/RECOVERED", "sameTxn": True},
    }
    results["Attempt"] += [check_sm08_event_binding(ATTEMPT_SM, attempt_events)]
    results["Attempt"] += [audit_dead_enum(ATTEMPT_SM)]
    return results


def all_ok(results: dict) -> bool:
    return all(c.ok for checks in results.values() for c in checks)


if __name__ == "__main__":
    results = run_all()
    lines = []
    lines.append("# 状态机模型检查报告（SM-xx，对齐手册 §13.1）\n")
    lines.append(f"- 生成时间：local（可复现：`.venv/bin/python scripts/sm_model.py`）")
    lines.append("- 范围：`pi_tasks`（Task）、`pi_attempts`（Attempt）转移白名单模型检查\n")
    for name, checks in results.items():
        lines.append(f"## {name}\n")
        for i, c in enumerate(checks, 1):
            tag = "PASS" if c.ok else "FAIL"
            detail = "; ".join(c.findings) if c.findings else "—"
            lines.append(f"- [{tag}] {c.__doc__ or 'detail'}（{detail}）")
        lines.append("")
    ok = all_ok(results)
    lines.append(f"## 结论\n\n**{'PASS' if ok else 'FAIL'}**（`all_ok={ok}`）\n")
    lines.append("## 实现差距注记（记录，不阻塞）\n")
    lines.append("""- SM-03：StateDeadlinePolicy 出口动作真实存在（用户 `CANCELLED`、worker 失败收敛、启动恢复 `FAILED`/`TERMINAL_REPORTED`），但触发时机为调用/启动时，**无常驻定时器**；运行时 deadline 定时留待调度/Gateway 预算层（蓝图 Gateway Journal/Budget）。
- Attempt 声明枚举 `RUNNING`/`FAILED` 为死枚举（`migrations/001_init.sql` 注释声明，worker 从未使用）；审计基线固化于 `tests/test_sm_model.py::test_dead_enum_audit_attempt`。对齐蓝图 Attempt 状态矩阵（CLAIMED/RUNNING/…/TERMINAL_REPORTED）时需扩充迁移点。
- 蓝图 Run / CandidateStagingOperation 状态机不在本实现范围（单任务直接执行模型）；`Run.state`/`selectedAttemptId` 等为蓝图 §7.7 后续层。""")
    out_dir = ROOT / "contracts" / "sm-model"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name, checks in results.items():
        print(f"== {name} ==")
        for c in checks:
            print(f"  {'PASS' if c.ok else 'FAIL'}: {'; '.join(c.findings) if c.findings else '—'}")
    print("\n总体:", "PASS" if ok else "FAIL")
    print("报告已写入:", out_dir / "report.md")