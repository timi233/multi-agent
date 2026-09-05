-- Pi 平台 Run 状态机（蓝图 §8.2 单机简化行进路径）与任务计划列
-- 简化边界：每任务每步骤至多一个 Run（UNIQUE(task_id, step_index)），
-- 无 Lease/Fencing（G6 补）、无 BLOCKED/REPAIR_REQUIRED/RETRY_WAIT 等待态
-- （顺序执行，依赖由步骤次序保证）；NO_VERDICT/HANDOFF_TO_HUMAN 为蓝图
-- 保留名，子集不达。

CREATE TABLE IF NOT EXISTS pi_runs (
    run_id                     TEXT PRIMARY KEY,
    task_id                    TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    step_index                 INT  NOT NULL CHECK (step_index >= 1),
    workflow_node_id           TEXT NOT NULL,
    run_kind                   TEXT NOT NULL,          -- IMPLEMENTATION|READ_ONLY|REVIEW
    deliverable_kind           TEXT NOT NULL,          -- CODE_CHANGE|REVIEW_EVIDENCE|READ_ONLY_EVIDENCE
    execution_plan_snapshot_id TEXT NOT NULL,          -- 所属 ExecutionPlanSnapshot（不可变计划引用）
    plan_digest                TEXT NOT NULL,          -- 计划 payloadDigest
    plan_payload               JSONB NOT NULL,         -- 计划完整签名快照（可审计、可验签）
    attempt_id                 TEXT,                   -- 当前执行该步的 Attempt
    status                     TEXT NOT NULL DEFAULT 'CREATED',
    -- CREATED|READY|EXECUTING|OUTPUT_STAGED|VERIFYING|VERIFIED|
    -- FAILED|BUDGET_EXHAUSTED|CANCELLED（过渡/终态白名单见 app/runtime/run_state.py）
    error_code                 TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at                TIMESTAMPTZ,
    UNIQUE (task_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_pi_runs_task_step ON pi_runs(task_id, step_index);

-- 任务可选携带编译前步骤（plan 字段，步骤数组）；NULL = 编译为默认单步计划
ALTER TABLE pi_tasks ADD COLUMN IF NOT EXISTS plan JSONB;