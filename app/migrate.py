"""数据库迁移（显式执行，禁止服务启动时隐式迁移 —— 手册规则）。"""
from __future__ import annotations

import psycopg

from .config import BASE_DIR, settings

MIGRATIONS_DIR = BASE_DIR / "migrations"


def migrate() -> None:
    conn = psycopg.connect(settings.db_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS pi_schema_migrations(filename TEXT PRIMARY KEY)"
            )
            applied = {row[0] for row in cur.execute("SELECT filename FROM pi_schema_migrations").fetchall()}
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if sql_file.name in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO pi_schema_migrations(filename) VALUES (%s)", (sql_file.name,)
                )
            conn.commit()
            print(f"[migrate] applied {sql_file.name}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()