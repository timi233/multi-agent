-- G5：本地 git 交付结果归档（蓝图 §10.10/§11 单机子集）
-- 每 task 独立 delivery repo（deliveries/<task_id>/，gitignore 排除）；
-- 每次确定性 staging 产出一条 GitStagingResult 证明：CommitBundle 绑定
-- commitBundleDigest，expected/applied ref 逐对象、gitStagingEpoch 操作代次、
-- operationIdempotencyKey 幂等（同 task 同键只允许一条结果）。

CREATE TABLE IF NOT EXISTS pi_git_staging_results (
    result_id               TEXT PRIMARY KEY,
    task_id                 TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    commit_bundle_id        TEXT NOT NULL CHECK (commit_bundle_id ~ '^[a-f0-9]{32}$'),
    commit_bundle_digest    TEXT NOT NULL CHECK (commit_bundle_digest ~ '^sha256:[a-f0-9]{64}$'),
    operation_idempotency_key TEXT NOT NULL CHECK (operation_idempotency_key ~ '^[a-f0-9]{32}$'),
    repository_id           TEXT NOT NULL CHECK (repository_id ~ '^[a-f0-9]{16}$'),
    candidate_ref           TEXT NOT NULL,
    applied_commit_id       TEXT NOT NULL CHECK (applied_commit_id ~ '^[a-f0-9]{40}$'),
    git_staging_epoch       INT NOT NULL CHECK (git_staging_epoch >= 1),
    result                  JSONB NOT NULL,           -- 完整签名 GitStagingResult 快照
    verified_ok             BOOLEAN NOT NULL DEFAULT FALSE,  -- 归档时契约语义校验结果
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, operation_idempotency_key)      -- 幂等：同键只允许一条
);
CREATE INDEX IF NOT EXISTS idx_pi_git_staging_task ON pi_git_staging_results(task_id, git_staging_epoch);