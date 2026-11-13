"""模拟外部表格同步适配器。

腾讯文档未接入，本适配器用**独立 SQLite 文件**模拟外部表格（不用内存，以便
验证程序重启后的恢复）。以稳定的 ``application_id`` 作为幂等键执行 upsert，
同一应聘记录重试或更新不重复新增外部记录。

页面与日志须明确标注“模拟外部表格”，不得声称腾讯文档已接通。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


class SyncError(Exception):
    """同步错误基类。"""


class TemporarySyncError(SyncError):
    """临时错误（可重试）。"""


class PermanentSyncError(SyncError):
    """永久错误（不可自动重试，需人工处理）。"""


class SyncAdapter:
    """统一同步适配器接口。"""

    name = "sync"

    def sync(self, record: dict[str, Any]) -> str:
        """把一条同步记录写入外部目标，返回外部记录标识（幂等键）。"""
        raise NotImplementedError


class MockSyncAdapter(SyncAdapter):
    """模拟外部表格：独立 SQLite 文件，按 application_id upsert。"""

    name = "mock_sync"

    def __init__(self, db_path: str, fail_first_n: int = 0, permanent_error: bool = False):
        self.db_path = db_path
        self.fail_first_n = fail_first_n
        self._failures_done = 0
        self.permanent_error = permanent_error
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS external_records ("
            "application_id TEXT PRIMARY KEY, candidate_id TEXT, position TEXT, channel TEXT, "
            "hr_owner TEXT, stage TEXT, interviews TEXT, updated_at TEXT)"
        )
        conn.commit()
        conn.close()

    def sync(self, record: dict[str, Any]) -> str:
        if self._failures_done < self.fail_first_n:
            self._failures_done += 1
            raise TemporarySyncError("模拟临时失败")
        if self.permanent_error:
            raise PermanentSyncError("模拟永久失败")

        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO external_records "
                "(application_id, candidate_id, position, channel, hr_owner, stage, interviews, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(application_id) DO UPDATE SET "
                "candidate_id=excluded.candidate_id, position=excluded.position, channel=excluded.channel, "
                "hr_owner=excluded.hr_owner, stage=excluded.stage, interviews=excluded.interviews, "
                "updated_at=datetime('now')",
                (
                    record["application_id"],
                    record.get("candidate_id"),
                    record.get("position"),
                    record.get("channel"),
                    record.get("hr_owner"),
                    record.get("stage"),
                    json.dumps(record.get("interviews", []), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return record["application_id"]

    def list_records(self) -> list[dict[str, Any]]:
        conn = self._connect()
        rows = conn.execute("SELECT * FROM external_records ORDER BY application_id").fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            d["interviews"] = json.loads(d["interviews"] or "[]")
            out.append(d)
        return out
