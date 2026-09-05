"""PostgreSQL 访问（psycopg 3，简单连接/上下文）。"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from .config import settings


def connect():
    return psycopg.connect(settings.db_dsn, row_factory=dict_row)


def execute(sql: str, params: tuple | None = None) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            if cur.description:
                return cur.fetchall()
            return []


def execute_one(sql: str, params: tuple | None = None) -> dict | None:
    rows = execute(sql, params)
    return rows[0] if rows else None