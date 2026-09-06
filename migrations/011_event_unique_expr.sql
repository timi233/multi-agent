-- G6（评审 block-3）：PostgreSQL 普通 UNIQUE 将 NULL 视为互不相同，而平台
-- 大量事件 attempt_id 为 NULL（api/worker 写入），普通约束拦不住重复——
-- 用表达式唯一索引把 NULL 归一为 '' 参与唯一判定：
-- (task_id, COALESCE(attempt_id,''), seq) 唯一，重复发布被唯一性吸收（幂等）。

ALTER TABLE pi_events DROP CONSTRAINT IF EXISTS uq_pi_events_task_attempt_seq;

DROP INDEX IF EXISTS uq_pi_events_task_attempt_seq_expr;
CREATE UNIQUE INDEX uq_pi_events_task_attempt_seq_expr
    ON pi_events (task_id, COALESCE(attempt_id, ''), seq);