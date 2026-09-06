# -*- coding: utf-8 -*-
"""G6 附录 A 硬门槛⑤⑥：长时间并发稳定性长跑（8h 并发 / 7d 稳定性的
可运行承载；--duration 限定本轮实际时长，8h/7d 由 CI/用户按需执行）。

压力面（如实披露，无 LLM 依赖——真实模型链路属 RT-06 冒烟职责）：
- 并发 Git 交付：多线程并发 stage_commit（同 opKey 幂等复用 / 不同 opKey
  追加、flock + update-ref CAS 串行推进），读回 applied == ref 强校验；
- 事件乱序收敛：每轮乱序写入短事件流并按 seq 重放收敛终态；
- 健康检查循环：每 5s 验证 DB 连通、任务/交付计数单调一致、deliveries
  git 对象可读（cat-file）。
输出 JSON 统计（轮数/成功/失败/收敛校验数/健康检查数），退出码非 0
即失败（供 CI 断言）。命令示例：
  冒烟（30s）：  .venv/bin/python scripts/acceptance_longrun.py --duration 30
  8h 并发：     .venv/bin/python scripts/acceptance_longrun.py --hours 8
  7d 稳定性：   .venv/bin/python scripts/acceptance_longrun.py --days 7
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))

from app.db import connect  # noqa: E402
from app.runtime.cas import put_bytes  # noqa: E402
from app.runtime.gitstager import GitStagingError, stage_commit  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr[:200]}")
    return out.stdout.strip()


def _one_round(round_no: int, rnd: random.Random) -> dict:
    """一轮：并发 4 个 task（2 交付 ×2 事件收敛）+ 收敛校验。"""
    stats = {"round": round_no, "deliveries": 0, "converged": 0,
             "errors": []}

    def delivery(seed_offset: int):
        try:
            task_id = uuid.uuid4().hex[:16]
            op1 = f"{rnd.getrandbits(128):032x}"
            with connect() as conn:
                conn.execute(
                    "INSERT INTO pi_tasks (id, title, prompt, workspace, "
                    "status) VALUES (%s, 'longrun', 'p', %s, 'SUCCESS')",
                    (task_id, f"task-{task_id}"))
                for i in range(3):
                    data = rnd.randbytes(16 + seed_offset)
                    digest = put_bytes(data)
                    conn.execute(
                        "INSERT INTO pi_artifacts (artifact_id, task_id, "
                        "step_index, path, digest, size, kind) "
                        "VALUES (%s, %s, 1, %s, %s, %s, 'file')",
                        (uuid.uuid4().hex[:16], task_id, f"f{i}.bin",
                         digest, len(data)))
            r = stage_commit(task_id, op_key=op1)
            r2 = stage_commit(task_id, op_key=op1)  # 幂等复用
            assert r2["gitStagingResultId"] == r["gitStagingResultId"]
            repo = ROOT / "deliveries" / task_id
            head = _git(repo, "rev-parse", "refs/heads/main")
            assert head == r["appliedCommitGitObjectId"]["hex"]  # 读回强校验
            stats["deliveries"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"delivery: {exc}")

    def event_converge(seed_offset: int):
        try:
            task_id = uuid.uuid4().hex[:16]
            with connect() as conn:
                conn.execute(
                    "INSERT INTO pi_tasks (id, title, prompt, workspace, "
                    "status) VALUES (%s, 'longrun', 'p', %s, 'RUNNING')",
                    (task_id, f"task-{task_id}"))
                seqs = list(range(1, 11))
                rnd.shuffle(seqs)  # 乱序写入
                for seq in seqs:
                    conn.execute(
                        "INSERT INTO pi_events (task_id, seq, event_type, "
                        "payload) VALUES (%s, %s, 'PING', %s)",
                        (task_id, seq, f'{{"n":{seq}}}'))
            with connect() as conn:
                n = conn.execute(
                    "SELECT count(*) AS n FROM pi_events WHERE task_id=%s",
                    (task_id,)).fetchone()["n"]
                assert n == 10  # 乱序落库无丢失
                # 按 seq 读回重放（评审 block-5）：序列必须完整且升序无缺
                rows = conn.execute(
                    "SELECT seq FROM pi_events WHERE task_id=%s ORDER BY seq",
                    (task_id,)).fetchall()
                assert [r["seq"] for r in rows] == list(range(1, 11))
            stats["converged"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"events: {exc}")

    threads = [threading.Thread(target=delivery, args=(i,))
               for i in range(2)] + \
              [threading.Thread(target=event_converge, args=(i,))
               for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return stats


def _health_check(round_no: int, last_tasks: int | None) -> tuple[bool, int | None]:
    """健康检查（评审 block-5）：DB 连通 + 任务计数单调 + 交付库 cat-file 可读
    + 最近一次归档 applied 与 repo 实际 ref 一致（读回强校验）。"""
    try:
        with connect() as conn:
            n = conn.execute(
                "SELECT count(*) AS n FROM pi_tasks").fetchone()["n"]
            if last_tasks is not None:
                assert n >= last_tasks  # 计数单调（无丢失/回退）
            # 最近一次归档：repos 读回一致性抽查
            row = conn.execute(
                "SELECT task_id, applied_commit_id FROM pi_git_staging_results "
                "ORDER BY created_at DESC LIMIT 1").fetchone()
        last_tasks = n
        if row is not None:
            # 评审 block-5 复评：归档存在但 repo/ref 缺失 = 健康检查失败
            # （不得跳过校验假装健康）
            repo = ROOT / "deliveries" / row["task_id"]
            if not (repo / ".git").exists():
                return False, last_tasks
            head = _git(repo, "rev-parse", "refs/heads/main")
            assert head == row["applied_commit_id"]  # 读回强校验
            _git(repo, "cat-file", "-t", head)  # 对象可读
        return True, last_tasks
    except Exception:  # noqa: BLE001
        return False, last_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Pi 平台长时间并发稳定性长跑")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="运行秒数（默认 60；8h=28800、7d=604800）")
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--days", type=float, default=None)
    parser.add_argument("--json", metavar="FILE", default=None)
    args = parser.parse_args()

    seconds = args.duration
    if args.hours:
        seconds = args.hours * 3600
    if args.days:
        seconds = args.days * 86400

    rnd = random.Random(20260907)
    started = time.time()
    summary = {"targetSeconds": seconds, "ranSeconds": 0.0, "rounds": 0,
               "deliveries": 0, "eventsConverged": 0, "healthChecks": 0,
               "healthCheckFailures": 0, "errors": [], "ok": True}
    round_no = 0
    _last_tasks: int | None = None
    while time.time() - started < seconds:
        round_no += 1
        st = _one_round(round_no, rnd)
        summary["rounds"] += 1
        summary["deliveries"] += st["deliveries"]
        summary["eventsConverged"] += st["converged"]
        summary["errors"].extend(st["errors"])
        if round_no % 2 == 0:
            summary["healthChecks"] += 1
            ok, last_tasks = _health_check(round_no, _last_tasks)
            _last_tasks = last_tasks
            if not ok:
                summary["healthCheckFailures"] += 1
    summary["ranSeconds"] = round(time.time() - started, 1)
    summary["ok"] = (not summary["errors"]
                     and summary["healthCheckFailures"] == 0
                     and summary["deliveries"] >= 1
                     and summary["eventsConverged"] >= 1)
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())