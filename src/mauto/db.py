"""SQLite 访问层：线程内连接 + 轻量查询助手。"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = settings.data_dir
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "mauto.db"

_local = threading.local()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}" if prefix else raw


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db() -> None:
    conn = get_conn()
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    # 轻量迁移：为已存在的库补齐任务产出相关字段
    additions = {
        "task": {"output_conversation_id": "TEXT"},
        "task_run": {
            "summary": "TEXT NOT NULL DEFAULT ''",
            "conversation_id": "TEXT",
            "message_id": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def q(sql: str, args: tuple | list = ()) -> list[dict]:
    cur = get_conn().execute(sql, args)
    return [dict(r) for r in cur.fetchall()]


def q1(sql: str, args: tuple | list = ()) -> dict | None:
    cur = get_conn().execute(sql, args)
    row = cur.fetchone()
    return dict(row) if row else None


def ex(sql: str, args: tuple | list = ()) -> int:
    conn = get_conn()
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.lastrowid if cur.lastrowid is not None else cur.rowcount


def exm(sql: str, seq: list) -> None:
    conn = get_conn()
    conn.executemany(sql, seq)
    conn.commit()


def insert(table: str, data: dict) -> str:
    keys = list(data.keys())
    sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})"
    ex(sql, [data[k] for k in keys])
    return str(data.get("id", ""))


def update(table: str, row_id: str, data: dict) -> None:
    if not data:
        return
    sets = ",".join(f"{k}=?" for k in data)
    ex(f"UPDATE {table} SET {sets} WHERE id=?", [*data.values(), row_id])


def jload(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def days_ago(days: float) -> str:
    return iso(datetime.now(timezone.utc) - timedelta(days=days))


def acquire_lock(name: str, blocking: bool = True):
    """跨进程文件锁（多 worker 启动时避免重复初始化）。

    返回需要保持引用的句柄；非阻塞获取失败返回 None；不支持 fcntl 的平台返回 True。
    """
    try:
        import fcntl
    except ImportError:  # Windows 本地开发：退化为单进程假设
        return True
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(DATA_DIR / f"{name}.lock", "w")
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(handle, flags)
    except OSError:
        handle.close()
        return None
    return handle


def release_lock(handle) -> None:
    if handle is None or handle is True:
        return
    try:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()
