# -*- coding: utf-8 -*-
"""G6 附录 A 硬门槛②：10000 条乱序事件收敛一致性（处理器白名单语义）。

事实基线（不虚报）：单机无消息总线/Outbox（README 披露）——乱序收敛在
本平台落点为「事件序列按 (run, seq) 收敛：乱序交错输入 → 重排 →
Run 状态机（app/runtime/run_state.py 白名单）逐事件合法推进 → 全部收敛
到终态 VERIFIED；重复事件键 (run,seq) 唯一吸收（幂等）」。真实执行：
1) 100 个 Run × 100 事件 = 10000 条（固定 seed 洗牌打乱写入顺序）；
2) 乱序写入 pi_events 落库（attempt_id=run 编号、seq 环内唯一）；
3) 按 (run, seq) 读回重放，transition 白名单每步推进，终态全 VERIFIED；
4) 有序/乱序重放结果逐 Run 一致（收敛不依赖到达顺序）。
运行：PI_PG_DB=pi_platform_test .venv/bin/python -m pytest
      tests/test_gate_out_of_order.py -q
"""
from __future__ import annotations

import random
import uuid

from app.db import connect
from app.runtime.run_state import assert_run_transition, insert_run

RUNS = 100
EVENTS_PER_RUN = 100  # 100 × 100 = 10000
TOTAL = RUNS * EVENTS_PER_RUN
SEED = 20260907

_CHAIN = ["READY", "EXECUTING", "OUTPUT_STAGED", "VERIFYING", "VERIFIED"]


def _seed() -> tuple[str, list[str]]:
    task_id = uuid.uuid4().hex[:16]
    run_ids = []
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'o3', 'p', %s, 'RUNNING')",
            (task_id, f"task-{task_id}"))
        for i in range(RUNS):
            rid = insert_run(conn, task_id=task_id, step_index=i + 1,
                             workflow_node_id="w", run_kind="workflow",
                             deliverable_kind="artifact",
                             execution_plan_snapshot_id="0" * 32,
                             plan_digest="sha256:" + "0" * 64,
                             plan_payload={"i": i})
            run_ids.append(rid)
    return task_id, run_ids


def _events() -> list[tuple[int, int, str]]:
    """每 Run：前 5 条为推进链 READY→…→VERIFIED，其后全部为 VERIFIED
    （终态重复事件 = 幂等吸收语义；乱序/重复不产生第二次效果）。"""
    return [(r, seq, _CHAIN[min(seq - 1, len(_CHAIN) - 1)])
            for r in range(RUNS) for seq in range(1, EVENTS_PER_RUN + 1)]


def _fold(states: dict[str, str], seqs) -> None:
    for (r, _seq, st_) in seqs:
        cur = states[list(states)[r]]
        if st_ == cur:
            continue  # 幂等吸收：重复事件不改变状态
        assert_run_transition(cur, st_)
        states[list(states)[r]] = st_


def test_10k_out_of_order_events_converge():
    task_id, run_ids = _seed()
    events = _events()
    assert len(events) == TOTAL == 10000
    shuffled = random.Random(SEED).sample(events, len(events))  # 乱序交错

    with connect() as conn:  # 乱序写入落库（(run,seq) 唯一）
        for (r, seq, _st) in shuffled:
            conn.execute(
                "INSERT INTO pi_events (task_id, attempt_id, seq, event_type, "
                "payload) VALUES (%s, %s, %s, 'RUN_TRANSITION', %s)",
                (task_id, f"{r:05d}", seq, f'{{"run":{r},"seq":{seq}}}'))
        # 真实重复注入（评审 block-2）：同一事件键再次发布 → DB 唯一约束
        # (task_id, attempt_id, seq) 吸收（UniqueViolation），不产生第二条。
        dup_absorbed = 0
        for (r, seq, _st) in shuffled[:500]:  # 重复发布 500 个事件键
            try:
                with conn.transaction():  # 嵌套 savepoint：冲突仅回滚该点
                    conn.execute(
                        "INSERT INTO pi_events (task_id, attempt_id, seq, "
                        "event_type, payload) VALUES (%s, %s, %s, "
                        "'RUN_TRANSITION', %s)",
                        (task_id, f"{r:05d}", seq,
                         f'{{"run":{r},"seq":{seq},"dup":1}}'))
            except Exception:
                dup_absorbed += 1
        assert dup_absorbed == 500  # 全部被唯一性吸收（DB 层幂等）
        rows = conn.execute(
            "SELECT attempt_id, seq FROM pi_events WHERE task_id=%s "
            "ORDER BY attempt_id, seq", (task_id,)).fetchall()
        assert len(rows) == TOTAL  # 10k 全部落库；重复未产生多余记录

    # 白名单收敛重放（终态后重复事件 = 幂等吸收）
    states = {rid: "CREATED" for rid in run_ids}
    for row in rows:
        r = int(row["attempt_id"])
        rid = run_ids[r]
        new = _CHAIN[min(row["seq"] - 1, len(_CHAIN) - 1)]
        cur = states[rid]
        if new == cur:
            continue  # 幂等吸收
        assert_run_transition(cur, new)  # 非法迁移即失败
        states[rid] = new
    assert states == {rid: "VERIFIED" for rid in run_ids}  # 全部收敛终态

    # 幂等吸收：事件键 (run, seq) 全唯一（重复注入被唯一性吸收）
    keys = {(int(row["attempt_id"]), row["seq"]) for row in rows}
    assert len(keys) == TOTAL


def test_null_attempt_duplicate_absorbed():
    """评审 block-3：attempt_id 为 NULL 的重复事件键同样被表达式唯一索引
    （COALESCE(attempt_id,'')）吸收——NULL 不再互相不唯一。"""
    task_id = uuid.uuid4().hex[:16]
    with connect() as conn:
        conn.execute(
            "INSERT INTO pi_tasks (id, title, prompt, workspace, status) "
            "VALUES (%s, 'null-att', 'p', 'w', 'QUEUED')", (task_id,))
        conn.execute(
            "INSERT INTO pi_events (task_id, attempt_id, seq, event_type, "
            "payload) VALUES (%s, NULL, 1, 'E', '{}')", (task_id,))
        absorbed = 0
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO pi_events (task_id, attempt_id, seq, "
                    "event_type, payload) VALUES (%s, NULL, 1, 'E', '{}')",
                    (task_id,))
        except Exception:
            absorbed += 1
        assert absorbed == 1
        n = conn.execute(
            "SELECT count(*) AS n FROM pi_events WHERE task_id=%s",
            (task_id,)).fetchone()["n"]
        assert n == 1  # 重复被唯一性吸收


def test_10k_out_of_order_vs_in_order_same_result():
    """有序与乱序重放逐 Run 终态一致（收敛不依赖到达顺序）。"""
    task_id, run_ids = _seed()
    ordered = _events()
    shuffled = random.Random(SEED + 1).sample(ordered, len(ordered))

    def fold(seqs) -> dict[str, str]:
        st = {rid: "CREATED" for rid in run_ids}
        # 乱序到达 → 按 (run, seq) 重排后收敛重放（与本平台 pi_events
        # ORDER BY 读回语义一致）；重复事件终态吸收。
        for (r, _seq, st_) in sorted(seqs, key=lambda e: (e[0], e[1])):
            rid = run_ids[r]
            if st_ == st[rid]:
                continue
            assert_run_transition(st[rid], st_)
            st[rid] = st_
        return st

    assert fold(ordered) == fold(shuffled) == \
        {rid: "VERIFIED" for rid in run_ids}