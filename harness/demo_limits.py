"""公开演示的服务端访问限制（消息长度 / 调用频率 / 总调用次数）。

使用独立 SQLite 文件记录调用，跨 Streamlit 会话共享（单实例），实现全局限制。
注意：宿主平台若文件系统不持久，限制计数会在重启后重置（与演示数据一起）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

TZ_SHANGHAI = timezone(timedelta(hours=8))


class DemoLimiter:
    """服务端调用限制器：记录每次 AI 调用，按窗口/总量限制。"""

    def __init__(
        self,
        db_path: str,
        *,
        max_total: int = 100,
        max_per_window: int = 10,
        window_seconds: int = 3600,
    ):
        self.db_path = db_path
        self.max_total = max_total
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS demo_calls ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, call_at TEXT NOT NULL)"
        )
        conn.commit()
        conn.close()

    def total_calls(self) -> int:
        conn = self._connect()
        n = conn.execute("SELECT COUNT(*) FROM demo_calls").fetchone()[0]
        conn.close()
        return n

    def recent_calls(self, now: datetime) -> int:
        window_start = (now - timedelta(seconds=self.window_seconds)).astimezone(
            TZ_SHANGHAI
        ).isoformat()
        conn = self._connect()
        n = conn.execute(
            "SELECT COUNT(*) FROM demo_calls WHERE call_at > ?", (window_start,)
        ).fetchone()[0]
        conn.close()
        return n

    def check_and_consume(
        self, text: str, max_len: int, now: datetime | None = None
    ) -> tuple[bool, str]:
        """检查消息长度/频率/总量；若允许则记录一次调用。返回 (allowed, reason)。"""
        now = (now or datetime.now(TZ_SHANGHAI)).astimezone(TZ_SHANGHAI)

        if len(text) > max_len:
            return False, f"消息过长（超过 {max_len} 字符）"
        if self.total_calls() >= self.max_total:
            return False, "已达到演示总调用次数上限"
        if self.recent_calls(now) >= self.max_per_window:
            return False, "演示调用频率超限，请稍后再试"

        conn = self._connect()
        conn.execute(
            "INSERT INTO demo_calls (call_at) VALUES (?)",
            (now.astimezone(TZ_SHANGHAI).isoformat(),),
        )
        conn.commit()
        conn.close()
        return True, ""
