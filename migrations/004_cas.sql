-- Pi 平台 Content-Addressable Artifact Store（蓝图 §6.9/§9.5/§13.2 单机子集）
-- 简化边界：本地目录 blob CAS（data/cas，gitignore 排除 data/），替代 MinIO；
-- 无对象存储凭据/分片/复制；digest=sha256 内容寻址，同内容天然去重。

CREATE TABLE IF NOT EXISTS pi_cas_blobs (
    digest       TEXT PRIMARY KEY CHECK (digest ~ '^sha256:[a-f0-9]{64}$'),
    size         BIGINT NOT NULL CHECK (size >= 0),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 产物引用（content-addressed 审计：digest 指向 CAS blob；envelope 指向
-- 校验通过的 AttemptTerminalEnvelope 快照）
CREATE TABLE IF NOT EXISTS pi_artifacts (
    artifact_id   TEXT PRIMARY KEY,           -- uuid16hex
    task_id       TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    run_id        TEXT,
    step_index    INT CHECK (step_index >= 1),
    path          TEXT NOT NULL,              -- 工作区相对路径
    digest        TEXT NOT NULL REFERENCES pi_cas_blobs(digest),
    size          BIGINT NOT NULL CHECK (size >= 0),
    kind          TEXT NOT NULL CHECK (kind IN ('file', 'dir')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pi_artifacts_task ON pi_artifacts(task_id, step_index);

-- 终态信封快照（可审计、可验签）
CREATE TABLE IF NOT EXISTS pi_terminal_envelopes (
    envelope_id   TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    attempt_id    TEXT NOT NULL,
    run_id        TEXT,
    step_index    INT CHECK (step_index >= 1),
    outcome_class TEXT NOT NULL,
    status        TEXT NOT NULL,
    envelope      JSONB NOT NULL,             -- 完整签名快照
    verified_ok   BOOLEAN NOT NULL DEFAULT FALSE,  -- 归档时契约语义校验结果
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pi_terminal_envelopes_task ON pi_terminal_envelopes(task_id, step_index);