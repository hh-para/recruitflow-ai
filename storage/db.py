"""SQLite 连接与建表。

设计约定：
- 业务标识（candidate_id / application_id / interview_id / message_id）以 TEXT 主键或唯一列存储，
  与 fixtures 中的稳定标签解耦：内部可直接采用 C001/A001/I001，也可使用其他标识。
- 时间字段保存带时区偏移的 ISO 8601 字符串（与 fixtures 一致，如 ``2026-11-16T09:00:00+08:00``）。
- JSON 字段以 TEXT 存储，序列化/反序列化由 Repository 负责。
- ``isolation_level=None`` 表示自动提交模式，事务由调用方显式 ``BEGIN``/``COMMIT``/``ROLLBACK`` 控制。
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT PRIMARY KEY,
    candidate_id   TEXT NOT NULL REFERENCES candidates(candidate_id),
    position       TEXT NOT NULL,
    channel        TEXT NOT NULL,
    hr_owner       TEXT NOT NULL,
    stage          TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (candidate_id, position)
);

CREATE TABLE IF NOT EXISTS interviews (
    interview_id  TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(application_id),
    round         INTEGER NOT NULL,
    scheduled_at  TEXT NOT NULL,
    interviewer   TEXT NOT NULL,
    status        TEXT NOT NULL,
    result        TEXT,
    UNIQUE (application_id, round)
);

CREATE TABLE IF NOT EXISTS raw_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL,
    source      TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    sender_id   TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    text        TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at TEXT,
    UNIQUE (source, group_id, message_id)
);

CREATE TABLE IF NOT EXISTS business_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id            TEXT NOT NULL,
    application_id        TEXT,
    action                TEXT NOT NULL,
    application_changes   TEXT NOT NULL,
    interview_after       TEXT,
    references_message_id TEXT,
    application_before    TEXT,
    interview_before      TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id               TEXT NOT NULL UNIQUE,
    status                   TEXT NOT NULL,
    reason                   TEXT NOT NULL,
    possible_application_ids TEXT NOT NULL,
    resolved_by              TEXT,
    pending_event            TEXT,
    pending_sent_at          TEXT,
    group_id                 TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_tasks (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id               TEXT NOT NULL UNIQUE,
    business_event_id     INTEGER REFERENCES business_events(id),
    target                TEXT NOT NULL,
    status                TEXT NOT NULL,
    attempts              INTEGER NOT NULL DEFAULT 0,
    last_error            TEXT,
    external_record_id    TEXT,
    application_id        TEXT,
    next_retry_at         TEXT,
    completed_at          TEXT,
    processing_started_at TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# T04/T05 在既有表上新增的列，供旧数据库安全迁移（不删除已有数据）。
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("raw_messages", "processed_at", "TEXT"),
    ("reviews", "pending_event", "TEXT"),
    ("reviews", "pending_sent_at", "TEXT"),
    ("reviews", "group_id", "TEXT"),
    ("business_events", "application_before", "TEXT"),
    ("business_events", "interview_before", "TEXT"),
    ("sync_tasks", "application_id", "TEXT"),
    ("sync_tasks", "next_retry_at", "TEXT"),
    ("sync_tasks", "completed_at", "TEXT"),
    ("sync_tasks", "processing_started_at", "TEXT"),
)


def migrate_db(conn: sqlite3.Connection) -> None:
    """为旧数据库补齐新增列；缺列才 ALTER，已有数据不受影响。"""
    for table, column, ctype in _MIGRATIONS:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")


def connect(path: str | sqlite3.Connection = ":memory:") -> sqlite3.Connection:
    """建立 SQLite 连接并启用外键与 Row 工厂。

    ``path`` 可以是文件路径（支持 ``Path``）、``":memory:"``，
    或一个已存在的连接（直接返回，便于复用）。
    """
    if isinstance(path, sqlite3.Connection):
        return path
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | sqlite3.Connection = ":memory:") -> sqlite3.Connection:
    """建立连接并执行建表脚本（含旧库安全迁移），返回已初始化的连接。"""
    conn = connect(path)
    conn.executescript(SCHEMA)
    migrate_db(conn)
    return conn
