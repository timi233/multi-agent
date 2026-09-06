-- Pi 平台 Skill 供应链（蓝图 §7/§10.4/§12.3 单机子集）
-- 简化边界：单一环境作用域 'local' ACTIVE 指针；无 CANARY/隔离/Revocation
-- 记录（恢复通过新版本+新审批完成）；决策槽 UNIQUE(proposal_id, role) 强制
-- 双 approver 职责分离（同一身份不能填充两个必需槽由服务层拒绝）。

CREATE TABLE IF NOT EXISTS pi_skill_packages (
    skill_package_id   TEXT PRIMARY KEY,          -- uuid16hex
    name               TEXT NOT NULL,
    version            TEXT NOT NULL,
    description        TEXT,
    source_dir         TEXT NOT NULL,             -- 相对 skills/ 的目录
    package_digest     TEXT NOT NULL CHECK (package_digest ~ '^sha256:[a-f0-9]{64}$'),
    entrypoint_path    TEXT NOT NULL DEFAULT 'SKILL.md',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS pi_approval_proposals (
    proposal_id    TEXT PRIMARY KEY,
    subject_type   TEXT NOT NULL CHECK (subject_type = 'SKILL_BUNDLE'),
    subject_id     TEXT NOT NULL,                -- bundle 内容组合身份（artifact+manifest）
    subject_digest TEXT NOT NULL,
    bundle_name    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING', 'QUORUM_REACHED', 'REJECTED')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pi_approval_decisions (
    decision_id      TEXT PRIMARY KEY,
    proposal_id      TEXT NOT NULL REFERENCES pi_approval_proposals(proposal_id) ON DELETE CASCADE,
    approval_role    TEXT NOT NULL CHECK (approval_role IN ('FUNCTION_APPROVER', 'SECURITY_APPROVER')),
    approver_identity TEXT NOT NULL,
    approved         BOOLEAN NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (proposal_id, approval_role)          -- 决策不可覆盖，改决定必须创建新提案
);

CREATE TABLE IF NOT EXISTS pi_skill_bundle_snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    bundle_name       TEXT NOT NULL,
    bundle_revision   INT NOT NULL,
    approval_proposal_id TEXT NOT NULL REFERENCES pi_approval_proposals(proposal_id),
    artifact_digest   TEXT NOT NULL CHECK (artifact_digest ~ '^sha256:[a-f0-9]{64}$'),
    manifest_digest   TEXT NOT NULL CHECK (manifest_digest ~ '^sha256:[a-f0-9]{64}$'),
    package_versions  JSONB NOT NULL,             -- 规范化 packageVersions 数组
    envelope          JSONB NOT NULL,             -- 完整签名 Snapshot
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pi_skill_snapshots_name ON pi_skill_bundle_snapshots(bundle_name, bundle_revision);

CREATE TABLE IF NOT EXISTS pi_skill_publication_pointers (
    env_scope    TEXT PRIMARY KEY,                -- 单机仅 'local'
    snapshot_id  TEXT NOT NULL REFERENCES pi_skill_bundle_snapshots(snapshot_id),
    state        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (state IN ('ACTIVE', 'RETIRED')),
    row_version  INT NOT NULL DEFAULT 1,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);