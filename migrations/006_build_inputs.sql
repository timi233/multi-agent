-- G4 补充：提案绑定构建输入（发布时重放同字节 inputs，保证 artifact digest
-- 与审批时一致；避免从已发布快照反推——首次发布无历史快照）
ALTER TABLE pi_approval_proposals
    ADD COLUMN IF NOT EXISTS build_inputs JSONB;