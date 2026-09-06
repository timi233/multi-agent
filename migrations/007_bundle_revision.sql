-- G4：bundle revision 唯一约束（评审 should-3：发布 revision 同事务分配，
-- 约束兜底并发冲突；恢复通过新版本新审批）
ALTER TABLE pi_skill_bundle_snapshots
    ADD CONSTRAINT uq_skill_bundle_revision UNIQUE (bundle_name, bundle_revision);