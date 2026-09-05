# 状态机模型检查报告（SM-xx，对齐手册 §13.1）

- 生成时间：local（可复现：`.venv/bin/python scripts/sm_model.py`）
- 范围：`pi_tasks`（Task）、`pi_attempts`（Attempt）转移白名单模型检查

## Task

- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)  ⚠ 1 项差距: SM-08 已知差距: Task QUEUED->RUNNING 事件(ATTEMPT_STARTED)跨事务——领取事务不写事件；attempt/事件在后续初始化事务（worker._run_task 153-160）
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)（（无声明枚举，跳过审计））

## Attempt

- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)
- [PASS] CheckResult(ok: 'bool', findings: 'list[str]' = <factory>, warnings: 'list[str]' = <factory>)（死枚举: RUNNING, FAILED）

## 结论

**PASS**（违规 0 项；已知差距 1 项，不计入通过判定）

## 实现差距注记（记录，不阻塞）

- SM-03：StateDeadlinePolicy 出口动作真实存在（用户 `CANCELLED`、worker 失败收敛、启动恢复 `FAILED`/`TERMINAL_REPORTED`），但触发时机为调用/启动时，**无常驻定时器**；运行时 deadline 定时留待调度/Gateway 预算层（蓝图 Gateway Journal/Budget）。
- Attempt 声明枚举 `RUNNING`/`FAILED` 为死枚举（`migrations/001_init.sql` 注释声明，worker 从未使用）；审计基线固化于 `tests/test_sm_model.py::test_dead_enum_audit_attempt`。对齐蓝图 Attempt 状态矩阵（CLAIMED/RUNNING/…/TERMINAL_REPORTED）时需扩充迁移点。
- 蓝图 Run / CandidateStagingOperation 状态机不在本实现范围（单任务直接执行模型）；`Run.state`/`selectedAttemptId` 等为蓝图 §7.7 后续层。
