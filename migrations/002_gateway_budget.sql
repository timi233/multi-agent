-- Pi 平台 Gateway 预算与 Journal（蓝图 §13.5/§18.2-18.3、手册 GW-xx）
-- 简化边界：单实例单代次，无 Ledger Service 分片；grant 全局唯一、每 attempt 一个。
-- 链式 Journal：previousEntryDigest/entryDigest 追加写（截断/篡改可检测，GW-04 精神）。

CREATE TABLE IF NOT EXISTS gw_budget_grants (
    id                  TEXT PRIMARY KEY,       -- grantId
    task_id             TEXT NOT NULL REFERENCES pi_tasks(id) ON DELETE CASCADE,
    attempt_id          TEXT,
    total_budget_tokens BIGINT NOT NULL CHECK (total_budget_tokens >= 0),
    consumed_tokens     BIGINT NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE|EXHAUSTED|SETTLED
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS gw_journal (
    seq                   BIGSERIAL PRIMARY KEY,    -- 追加序号（写入顺序）
    grant_id              TEXT NOT NULL REFERENCES gw_budget_grants(id) ON DELETE CASCADE,
    entry_type            TEXT NOT NULL,            -- RESERVED|SENT|SETTLED|FAILED
    invocation_id         TEXT NOT NULL,            -- 每次 LLM 调用的事实 ID（GW-06：同 invocationId 不同 requestDigest 拒绝）
    request_digest        TEXT,
    reserved_tokens       BIGINT NOT NULL DEFAULT 0 CHECK (reserved_tokens >= 0),
    actual_tokens         BIGINT CHECK (actual_tokens IS NULL OR actual_tokens >= 0),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    previous_entry_digest TEXT NOT NULL,
    entry_digest          TEXT NOT NULL,
    UNIQUE (grant_id, invocation_id, entry_type)     -- 每类型每调用仅一条（防重复预留/结算）
);

CREATE INDEX IF NOT EXISTS idx_gw_journal_grant_seq ON gw_journal(grant_id, seq);