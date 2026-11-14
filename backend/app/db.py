"""SQLite 访问层：单文件库 + WAL，短事务。

R1.0 用 SQLite 的原因：零运维、可直接拷走做可行性验证；
字段/校正表结构与后续 MySQL 版本保持一致，迁移只需换 DDL 方言。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .config import settings

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_conn() -> sqlite3.Connection:
    settings.ensure_dirs()
    conn = sqlite3.connect(str(settings.db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = _new_conn()
        return _CONN


@contextmanager
def tx():
    """写事务：显式提交，异常回滚。"""
    conn = get_conn()
    with _LOCK:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def init_db() -> None:
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with tx() as conn:
        conn.executescript(schema)
        # 轻量迁移：老库补列（SQLite 不支持 IF NOT EXISTS 的 ADD COLUMN）
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(document)").fetchall()}
        if "first_decision" not in cols:
            conn.execute("ALTER TABLE document ADD COLUMN first_decision TEXT")


def query_all(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    cur = get_conn().execute(sql, tuple(params))
    return cur.fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    cur = get_conn().execute(sql, tuple(params))
    return cur.fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, tuple(params))
        return cur.lastrowid or 0


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default
