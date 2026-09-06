-- G4：审批决策同一身份不可重复（评审 block：四象职责分离 DB 兜底，
-- 服务层锁提案行先行检查）
ALTER TABLE pi_approval_decisions
    ADD CONSTRAINT uq_proposal_approver UNIQUE (proposal_id, approver_identity);