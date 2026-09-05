"""Gateway 预算域（蓝图 §13.5/§18.2-18.3 简化单实例版）。

BudgetDomain 封装"单 attempt 一份 BudgetGrant + 链式消费 Journal"：
- 调用前持久化预留（RESERVED）与发送意图（SENT）——kill 后预留在账（GW-03 精神）；
- 结算（SETTLED）累加 consumed；失败（FAILED）释放预留；
- Journal 追加写 + previousEntryDigest/entryDigest 链（截断/篡改可检测，GW-04 精神）；
- 同 invocationId 不同 requestDigest 拒绝（GW-06）；
- 可用预算 = total - consumed - outstanding；不足即 BudgetExceeded（GW-07 100% 阻断）。

简化边界（README 记录）：单实例单代次、无 Ledger Service 分片对账；事务由调用方
统一 commit（状态变化与 Journal 同一事务）。热路径每次操作一次 PG 往返（GW-10
"不查询 PostgreSQL"未达——记录为简化）。
"""
from __future__ import annotations

import hashlib
import uuid

ROOT_DIGEST = "pi-budget-root-v1"
JOURNAL_TYPES = ("RESERVED", "SENT", "SETTLED", "FAILED")


class BudgetError(Exception):
    pass


class BudgetExceeded(BudgetError):
    def __init__(self, needed: int, available: int):
        super().__init__(
            f"BUDGET_EXHAUSTED: need {needed} tokens, available {available}")
        self.needed = needed
        self.available = available


def _entry_digest(previous: str, grant_id: str, invocation_id: str,
                  entry_type: str, reserved: int, actual: int | None,
                  request_digest: str | None) -> str:
    raw = "|".join([
        previous, grant_id, invocation_id, entry_type, str(reserved),
        "" if actual is None else str(actual), request_digest or "",
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BudgetDomain:
    """单 attempt 预算域。所有方法接收已连接 conn，事务提交由调用方负责。"""

    def __init__(self, grant_id: str):
        self.grant_id = grant_id

    @classmethod
    def create(cls, conn, task_id: str, attempt_id: str | None,
               total_tokens: int, grant_id: str | None = None) -> "BudgetDomain":
        gid = grant_id or uuid.uuid4().hex[:16]
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gw_budget_grants "
                "(id, task_id, attempt_id, total_budget_tokens, status) "
                "VALUES (%s, %s, %s, %s, 'ACTIVE')",
                (gid, task_id, attempt_id, total_tokens))
        return cls(gid)

    # ---------- 余额 ----------

    def balance(self, conn) -> dict:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT total_budget_tokens, consumed_tokens, status "
                "FROM gw_budget_grants WHERE id=%s", (self.grant_id,))
            row = cur.fetchone()
            if not row:
                raise BudgetError(f"grant not found: {self.grant_id}")
            cur.execute(
                "SELECT "
                " COALESCE(SUM(CASE WHEN entry_type='RESERVED' "
                "   THEN reserved_tokens END), 0) AS reserved_sum, "
                " COALESCE(SUM(CASE WHEN entry_type='SETTLED' "
                "   THEN actual_tokens END), 0) "
                " + COALESCE(SUM(CASE WHEN entry_type='FAILED' "
                "   THEN reserved_tokens END), 0) AS released_sum "
                "FROM gw_journal WHERE grant_id=%s", (self.grant_id,))
            sums = cur.fetchone()
        total = row["total_budget_tokens"]
        consumed = row["consumed_tokens"]
        status = row["status"]
        reserved_sum = sums["reserved_sum"]
        released_sum = sums["released_sum"]
        outstanding = max(0, reserved_sum - released_sum)
        available = max(0, total - consumed - outstanding)
        return {
            "total": total, "consumed": consumed, "outstanding": outstanding,
            "available": available, "status": status,
        }

    # ---------- 预留 / 发送意图 / 结算 / 失败 ----------

    def reserve(self, conn, invocation_id: str, request_digest: str,
                tokens: int) -> None:
        """调用前持久化预留：可用不足即 BudgetExceeded；同 invocationId
        不同 requestDigest 拒绝（GW-06）。"""
        if tokens <= 0:
            raise BudgetError("reserve tokens must be > 0")
        bal = self.balance(conn)
        if bal["status"] != "ACTIVE":
            raise BudgetExceeded(needed=tokens, available=0)
        if bal["consumed"] + bal["outstanding"] + tokens > bal["total"]:
            raise BudgetExceeded(
                needed=tokens + bal["outstanding"],
                available=bal["available"])
        with conn.cursor() as cur:
            cur.execute(
                "SELECT request_digest FROM gw_journal "
                "WHERE grant_id=%s AND invocation_id=%s LIMIT 1",
                (self.grant_id, invocation_id))
            existing = cur.fetchone()
            if existing is not None and existing["request_digest"] != request_digest:
                raise BudgetError(
                    f"INVOCATION_DIGEST_MISMATCH: {invocation_id} "
                    f"existing={existing['request_digest']!r} new={request_digest!r}")
        self._append(conn, "RESERVED", invocation_id, request_digest,
                     tokens, None)

    def sent(self, conn, invocation_id: str) -> None:
        """发送意图持久化（dispatch 前）。仅记录意图，占额已由 RESERVED 承担，
        不重复计入 balance（outstanding 只聚合 RESERVED）。"""
        reserved = self._last_reserved_tokens(conn, invocation_id)
        self._append(conn, "SENT", invocation_id, None, 0, None)

    def settle(self, conn, invocation_id: str, actual_tokens: int) -> None:
        """结算：写 SETTLED 并累加 grant.consumed（同一事务由调用方提交）。"""
        if actual_tokens < 0:
            raise BudgetError("actual_tokens must be >= 0")
        self._append(conn, "SETTLED", invocation_id, None, 0, actual_tokens)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE gw_budget_grants SET consumed_tokens = consumed_tokens + %s "
                "WHERE id=%s", (actual_tokens, self.grant_id))

    def fail(self, conn, invocation_id: str) -> None:
        """失败：释放该调用的预留占额（不增加 consumed）。

        FAILED 条目在 reserved_tokens 记录被释放的预留量（balance 聚合中
        计入 released），使 outstanding 回落、占额重新可用。"""
        reserved = self._last_reserved_tokens(conn, invocation_id)
        self._append(conn, "FAILED", invocation_id, None, reserved, None)

    def settle_grant(self, conn) -> None:
        """attempt 结束：grant 置 SETTLED（终结，不可再预留）。"""
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE gw_budget_grants SET status='SETTLED', "
                "settled_at=now() WHERE id=%s", (self.grant_id,))

    # ---------- 链式 Journal ----------

    def _last_digest(self, conn) -> str:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT entry_digest FROM gw_journal WHERE grant_id=%s "
                "ORDER BY seq DESC LIMIT 1", (self.grant_id,))
            row = cur.fetchone()
        return row["entry_digest"] if row else ROOT_DIGEST

    def _last_reserved_tokens(self, conn, invocation_id: str) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reserved_tokens FROM gw_journal "
                "WHERE grant_id=%s AND invocation_id=%s AND entry_type='RESERVED' "
                "ORDER BY seq DESC LIMIT 1", (self.grant_id, invocation_id))
            row = cur.fetchone()
        if not row:
            raise BudgetError(f"no RESERVED for invocation {invocation_id}")
        return row["reserved_tokens"]

    def _append(self, conn, entry_type: str, invocation_id: str,
                request_digest: str | None, reserved: int,
                actual: int | None) -> None:
        if entry_type not in JOURNAL_TYPES:
            raise BudgetError(f"unknown entry_type: {entry_type}")
        previous = self._last_digest(conn)
        digest = _entry_digest(previous, self.grant_id, invocation_id,
                               entry_type, reserved, actual, request_digest)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO gw_journal (grant_id, entry_type, invocation_id, "
                "request_digest, reserved_tokens, actual_tokens, "
                "previous_entry_digest, entry_digest) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (self.grant_id, entry_type, invocation_id, request_digest,
                 reserved, actual, previous, digest))

    def reconcile(self, conn) -> list[str]:
        """完整复核（GW-04 对账）：链完整性 + consumed 与 ΣSETTLED 匹配。

        纯 Hash 链无法检出"删除中间/尾行"（链自身仍闭合）；截断通过
        grant.consumed 与 Journal 结算累计的对账暴露（结算只经 settle 更新）。"""
        problems = self.verify_chain(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT consumed_tokens FROM gw_budget_grants WHERE id=%s",
                (self.grant_id,))
            row = cur.fetchone()
            cur.execute(
                "SELECT COALESCE(SUM(actual_tokens),0) AS settled_sum "
                "FROM gw_journal "
                "WHERE grant_id=%s AND entry_type='SETTLED'",
                (self.grant_id,))
            settled_sum = cur.fetchone()["settled_sum"]
        if row["consumed_tokens"] != settled_sum:
            problems.append(
                f"对账不一致: grant.consumed={row['consumed_tokens']} "
                f"!= ΣSETTLED={settled_sum}（Journal 被截断或篡改）")
        return problems

    def verify_chain(self, conn) -> list[str]:
        """重放全部 Journal 校验链：链衔接 + entry_digest 重算（篡改检测）。

        纯 Hash 链无法检出"删除行"（链自身仍闭合）；截断由 reconcile 的
        consumed↔ΣSETTLED 对账暴露。"""
        problems: list[str] = []
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, entry_type, invocation_id, request_digest, "
                "reserved_tokens, actual_tokens, previous_entry_digest, "
                "entry_digest FROM gw_journal WHERE grant_id=%s ORDER BY seq",
                (self.grant_id,))
            rows = cur.fetchall()
        prev_expected = ROOT_DIGEST
        for row in rows:
            seq = row["seq"]
            etype = row["entry_type"]
            inv = row["invocation_id"]
            req = row["request_digest"]
            reserved = row["reserved_tokens"]
            actual = row["actual_tokens"]
            prev_dig = row["previous_entry_digest"]
            digest = row["entry_digest"]
            if prev_dig != prev_expected:
                problems.append(
                    f"seq {seq} 链断裂: previous={prev_dig!r} "
                    f"expected={prev_expected!r}（截断或篡改）")
            recomputed = _entry_digest(prev_dig, self.grant_id, inv, etype,
                                       reserved, actual, req)
            if recomputed != digest:
                problems.append(f"seq {seq} entry_digest 不匹配（篡改）")
            prev_expected = digest
        return problems