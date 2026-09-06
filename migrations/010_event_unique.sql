-- G6：乱序收敛幂等吸收（附录 A 硬门槛②）——唯一键第一步：普通 UNIQUE
-- 约束（NULL 语义修正见 011_event_unique_expr.sql，把约束替换为表达式索引）。

DELETE FROM pi_events a USING pi_events b
 WHERE a.id > b.id
   AND a.task_id = b.task_id
   AND COALESCE(a.attempt_id, '') = COALESCE(b.attempt_id, '')
   AND a.seq = b.seq;

ALTER TABLE pi_events
    ADD CONSTRAINT uq_pi_events_task_attempt_seq
    UNIQUE (task_id, attempt_id, seq);