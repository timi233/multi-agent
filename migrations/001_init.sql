-- Pi 平台 MVP 初始表结构（实验配置）
-- 说明：正式环境按手册 §4.1 应使用 svc_* 唯一写入者角色；MVP 阶段统一以 pi_admin 建列，
--       授权模型留待实现章程与蓝图对齐（属于已知简化，见 README）。
CREATE TABLE IF NOT EXISTS pi_tasks (
    id            TEXT PRIMARY KEY,             -- ULID/taskId
    title         TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    workspace     TEXT NOT NULL,                -- 相对 workspaces/ 的目录名
    status        TEXT NOT NULL DEFAULT 'QUEUED',  -- QUEUED|RUNNING|SUCCESS|FAILED|CANCELLED
    error         TEXT,
    model         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pi_attempts (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    number        INT  NOT NULL DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'CLAIMED',  -- CLAIMED|RUNNING|TERMINAL_REPORTED|FAILED
    trace_id      TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pi_events (
    id            BIGSERIAL PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    attempt_id    TEXT,
    seq           INT  NOT NULL DEFAULT 1,
    event_type    TEXT NOT NULL,               -- TASK_CREATED|ATTEMPT_STARTED|AGENT_TURN|TOOL_CALL|TOOL_RESULT|ATTEMPT_FINISHED|...
    payload       JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pi_events_task ON pi_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_pi_tasks_status ON pi_tasks(status, created_at DESC);