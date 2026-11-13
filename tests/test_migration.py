"""T06 旧数据库安全迁移测试（不删除已有数据）。"""
import sqlite3

from storage import init_db


def test_migrate_old_db_adds_columns_and_preserves_data(tmp_path):
    db_path = str(tmp_path / "old.db")

    # 构造“旧版”reviews / raw_messages（缺 T04/T05 新增列）
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE reviews ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE, "
        "status TEXT NOT NULL, reason TEXT NOT NULL, possible_application_ids TEXT NOT NULL, "
        "resolved_by TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.execute(
        "CREATE TABLE raw_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT NOT NULL, source TEXT NOT NULL, "
        "group_id TEXT NOT NULL, message_id TEXT NOT NULL, sent_at TEXT NOT NULL, "
        "sender_id TEXT NOT NULL, sender_name TEXT NOT NULL, text TEXT NOT NULL, "
        "received_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE (source, group_id, message_id))"
    )
    conn.execute(
        "INSERT INTO reviews (message_id, status, reason, possible_application_ids) "
        "VALUES ('M012', 'pending', 'candidate_ambiguous', '[]')"
    )
    conn.commit()
    conn.close()

    # init_db 会建缺失的表并安全迁移已有表的缺列
    conn2 = init_db(db_path)

    reviews_cols = {r[1] for r in conn2.execute("PRAGMA table_info(reviews)")}
    assert {"pending_event", "pending_sent_at", "group_id"} <= reviews_cols

    raw_cols = {r[1] for r in conn2.execute("PRAGMA table_info(raw_messages)")}
    assert "processed_at" in raw_cols

    # 旧数据保留
    row = conn2.execute("SELECT message_id, status FROM reviews WHERE message_id='M012'").fetchone()
    assert row["message_id"] == "M012"
    assert row["status"] == "pending"
    conn2.close()
