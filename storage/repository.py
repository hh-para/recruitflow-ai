"""数据访问仓储（Repository）。

对外提供候选、应聘记录、面试、原始消息、业务事件、待确认任务、同步任务的
新增、查询与局部更新方法，以及一个显式事务上下文管理器。

“字段未提供”与“显式清空”的区分：
- 局部更新方法（``update_*``）只更新 ``changes`` 中出现的键；未出现的键保持原值。
- 键值取 ``CLEAR`` 哨兵表示“显式清空”，将该列置为 NULL（仅对可空列有意义；
  清空非空列会触发 SQLite NOT NULL 约束错误）。

本层不含业务规则；匹配、去重、阶段流转等逻辑在 harness 中实现。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterable, Mapping

from .db import connect, init_db
from .errors import DuplicateError, NotFoundError


class _Clear:
    """显式清空哨兵。用于区分“字段未提供”与“字段被明确清空”。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅用于调试输出
        return "<CLEAR>"


CLEAR = _Clear()

# 各表允许通过局部更新修改的列白名单（防注入/防笔误）。
_ALLOWED_COLUMNS: dict[str, set[str]] = {
    "applications": {"candidate_id", "position", "channel", "hr_owner", "stage"},
    "interviews": {"application_id", "round", "scheduled_at", "interviewer", "status", "result"},
    "reviews": {"status", "reason", "possible_application_ids", "resolved_by"},
    "sync_tasks": {"status", "attempts", "last_error", "external_record_id", "application_id", "next_retry_at", "completed_at", "processing_started_at"},
}


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None) -> Any:
    return json.loads(value) if value is not None else None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class LedgerRepository:
    """包装单个 SQLite 连接的数据访问对象。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._in_transaction = False

    @property
    def in_transaction(self) -> bool:
        """当前是否处于显式写事务中（用于测试校验：模型调用期间不持有写事务）。"""
        return self._in_transaction

    # ------------------------------------------------------------------ 事务
    @contextmanager
    def transaction(self):
        """显式事务上下文：成功提交，异常回滚。

        本 Demo 的事务不嵌套；嵌套 BEGIN 会由 SQLite 抛出错误。
        """
        self.conn.execute("BEGIN")
        self._in_transaction = True
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False

    # -------------------------------------------------------------- 候选人
    def create_candidate(self, candidate_id: str, name: str) -> dict[str, Any]:
        self.conn.execute(
            "INSERT INTO candidates (candidate_id, name) VALUES (?, ?)",
            (candidate_id, name),
        )
        return self.get_candidate(candidate_id)

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT candidate_id, name FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_candidates_by_name(self, name: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT candidate_id, name FROM candidates WHERE name = ? ORDER BY candidate_id",
            (name,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_candidates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT candidate_id, name FROM candidates ORDER BY candidate_id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------ 应聘记录
    def create_application(
        self,
        application_id: str,
        candidate_id: str,
        position: str,
        channel: str,
        hr_owner: str,
        stage: str,
    ) -> dict[str, Any]:
        try:
            self.conn.execute(
                "INSERT INTO applications (application_id, candidate_id, position, channel, hr_owner, stage) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (application_id, candidate_id, position, channel, hr_owner, stage),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(
                f"应聘记录冲突：{candidate_id} 已存在岗位 {position!r} 的记录"
            ) from exc
        return self.get_application(application_id)

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT application_id, candidate_id, position, channel, hr_owner, stage, created_at "
            "FROM applications WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_application(self, candidate_id: str, position: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT application_id, candidate_id, position, channel, hr_owner, stage "
            "FROM applications WHERE candidate_id = ? AND position = ?",
            (candidate_id, position),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_applications(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT application_id, candidate_id, position, channel, hr_owner, stage, created_at "
            "FROM applications ORDER BY application_id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update_application(self, application_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        """局部更新应聘记录。仅更新 ``changes`` 中的键，其余字段保持原值。"""
        self._apply_update("applications", "application_id", application_id, changes)
        updated = self.get_application(application_id)
        if updated is None:
            raise NotFoundError(f"应聘记录不存在：{application_id}")
        return updated

    # ---------------------------------------------------------------- 面试
    def create_interview(
        self,
        interview_id: str,
        application_id: str,
        round: int,
        scheduled_at: str,
        interviewer: str,
        status: str,
        result: str | None = None,
    ) -> dict[str, Any]:
        self.conn.execute(
            "INSERT INTO interviews (interview_id, application_id, round, scheduled_at, interviewer, status, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (interview_id, application_id, round, scheduled_at, interviewer, status, result),
        )
        return self.get_interview(interview_id)

    def get_interview(self, interview_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT interview_id, application_id, round, scheduled_at, interviewer, status, result "
            "FROM interviews WHERE interview_id = ?",
            (interview_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_interview(self, application_id: str, round: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT interview_id, application_id, round, scheduled_at, interviewer, status, result "
            "FROM interviews WHERE application_id = ? AND round = ?",
            (application_id, round),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_interviews(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT interview_id, application_id, round, scheduled_at, interviewer, status, result "
            "FROM interviews ORDER BY interview_id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update_interview(self, interview_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        """局部更新面试。可对 ``result`` 等可空字段使用 ``CLEAR`` 显式清空。"""
        self._apply_update("interviews", "interview_id", interview_id, changes)
        updated = self.get_interview(interview_id)
        if updated is None:
            raise NotFoundError(f"面试不存在：{interview_id}")
        return updated

    # ------------------------------------------------------------ 原始消息
    def insert_raw_message(
        self,
        *,
        delivery_id: str,
        source: str,
        group_id: str,
        message_id: str,
        sent_at: str,
        sender_id: str,
        sender_name: str,
        text: str,
    ) -> dict[str, Any]:
        try:
            self.conn.execute(
                "INSERT INTO raw_messages (delivery_id, source, group_id, message_id, sent_at, sender_id, sender_name, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (delivery_id, source, group_id, message_id, sent_at, sender_id, sender_name, text),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(
                f"重复消息：source={source!r} group_id={group_id!r} message_id={message_id!r}"
            ) from exc
        row = self.conn.execute(
            "SELECT delivery_id, source, group_id, message_id, sent_at, sender_id, sender_name, text "
            "FROM raw_messages WHERE source = ? AND group_id = ? AND message_id = ?",
            (source, group_id, message_id),
        ).fetchone()
        return _row_to_dict(row) if row else {}

    def message_exists(self, source: str, group_id: str, message_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM raw_messages WHERE source = ? AND group_id = ? AND message_id = ?",
            (source, group_id, message_id),
        ).fetchone()
        return row is not None

    def list_raw_messages(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT delivery_id, source, group_id, message_id, sent_at, sender_id, sender_name, text, processed_at "
            "FROM raw_messages ORDER BY id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_raw_message(self, source: str, group_id: str, message_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT delivery_id, source, group_id, message_id, sent_at, sender_id, sender_name, text, processed_at "
            "FROM raw_messages WHERE source = ? AND group_id = ? AND message_id = ?",
            (source, group_id, message_id),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_raw_message_by_message_id(self, message_id: str) -> dict[str, Any] | None:
        """按 message_id 查找原始消息（演示环境 message_id 唯一）。"""
        row = self.conn.execute(
            "SELECT delivery_id, source, group_id, message_id, sent_at, sender_id, sender_name, text, processed_at "
            "FROM raw_messages WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def mark_raw_message_processed(self, source: str, group_id: str, message_id: str) -> None:
        """把原始消息标记为已处理（与业务写入在同一事务内完成）。"""
        self.conn.execute(
            "UPDATE raw_messages SET processed_at = datetime('now') "
            "WHERE source = ? AND group_id = ? AND message_id = ?",
            (source, group_id, message_id),
        )

    # ------------------------------------------------------------ 业务事件
    def insert_business_event(
        self,
        *,
        message_id: str,
        action: str,
        application_id: str | None = None,
        application_changes: Mapping[str, Any] | None = None,
        interview_after: Mapping[str, Any] | None = None,
        references_message_id: str | None = None,
        application_before: Mapping[str, Any] | None = None,
        interview_before: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cur = self.conn.execute(
            "INSERT INTO business_events "
            "(message_id, application_id, action, application_changes, interview_after, references_message_id, application_before, interview_before) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                application_id,
                action,
                _dumps(dict(application_changes or {})),
                _dumps(interview_after) if interview_after is not None else None,
                references_message_id,
                _dumps(dict(application_before)) if application_before is not None else None,
                _dumps(dict(interview_before)) if interview_before is not None else None,
            ),
        )
        row = self.conn.execute(
            "SELECT id, message_id, application_id, action, application_changes, interview_after, references_message_id, application_before, interview_before, created_at "
            "FROM business_events WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        d = _row_to_dict(row)
        d["application_changes"] = _loads(d["application_changes"])
        d["interview_after"] = _loads(d["interview_after"])
        d["application_before"] = _loads(d["application_before"])
        d["interview_before"] = _loads(d["interview_before"])
        return d

    def list_business_events(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, message_id, application_id, action, application_changes, interview_after, references_message_id, application_before, interview_before, created_at "
            "FROM business_events ORDER BY id"
        ).fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            d["application_changes"] = _loads(d["application_changes"])
            d["interview_after"] = _loads(d["interview_after"])
            d["application_before"] = _loads(d["application_before"])
            d["interview_before"] = _loads(d["interview_before"])
            out.append(d)
        return out

    # ------------------------------------------------------------ 待确认任务
    def create_review(
        self,
        *,
        message_id: str,
        reason: str,
        possible_application_ids: Iterable[str],
        pending_event: Mapping[str, Any] | None = None,
        pending_sent_at: str | None = None,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            self.conn.execute(
                "INSERT INTO reviews (message_id, status, reason, possible_application_ids, pending_event, pending_sent_at, group_id) "
                "VALUES (?, 'pending', ?, ?, ?, ?, ?)",
                (
                    message_id,
                    reason,
                    _dumps(list(possible_application_ids)),
                    _dumps(dict(pending_event)) if pending_event is not None else None,
                    pending_sent_at,
                    group_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"待确认任务已存在：message_id={message_id!r}") from exc
        return self.get_review(message_id)

    def get_review(self, message_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT message_id, status, reason, possible_application_ids, resolved_by, pending_event, pending_sent_at, group_id "
            "FROM reviews WHERE message_id = ?",
            (message_id,),
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["possible_application_ids"] = _loads(d["possible_application_ids"])
        d["pending_event"] = _loads(d["pending_event"])
        return d

    def list_reviews(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT message_id, status, reason, possible_application_ids, resolved_by, pending_event, pending_sent_at, group_id "
            "FROM reviews ORDER BY id"
        ).fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            d["possible_application_ids"] = _loads(d["possible_application_ids"])
            d["pending_event"] = _loads(d["pending_event"])
            out.append(d)
        return out

    def list_open_reviews(self) -> list[dict[str, Any]]:
        return [r for r in self.list_reviews() if r["status"] == "pending"]

    def resolve_review(self, message_id: str, resolved_by: str) -> dict[str, Any]:
        self.update_review(message_id, {"status": "resolved", "resolved_by": resolved_by})
        resolved = self.get_review(message_id)
        if resolved is None:
            raise NotFoundError(f"待确认任务不存在：{message_id}")
        return resolved

    def update_review(self, message_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        self._apply_update("reviews", "message_id", message_id, changes)
        updated = self.get_review(message_id)
        if updated is None:
            raise NotFoundError(f"待确认任务不存在：{message_id}")
        return updated

    # ------------------------------------------------------------ 同步任务
    def create_sync_task(
        self,
        *,
        task_id: str,
        business_event_id: int,
        target: str,
        status: str = "pending",
        application_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            self.conn.execute(
                "INSERT INTO sync_tasks (task_id, business_event_id, target, status, application_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, business_event_id, target, status, application_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateError(f"同步任务已存在：task_id={task_id!r}") from exc
        return self.get_sync_task(task_id)

    def get_sync_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT task_id, business_event_id, target, status, attempts, last_error, external_record_id, application_id, next_retry_at, completed_at, processing_started_at "
            "FROM sync_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_sync_tasks(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, task_id, business_event_id, target, status, attempts, last_error, external_record_id, application_id, next_retry_at, completed_at, processing_started_at "
            "FROM sync_tasks ORDER BY id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update_sync_task(self, task_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        # updated_at 不在白名单内，通过 SQL 单独刷新。
        self._apply_update("sync_tasks", "task_id", task_id, changes)
        self.conn.execute(
            "UPDATE sync_tasks SET updated_at = datetime('now') WHERE task_id = ?",
            (task_id,),
        )
        updated = self.get_sync_task(task_id)
        if updated is None:
            raise NotFoundError(f"同步任务不存在：{task_id}")
        return updated

    # ------------------------------------------------------------ 内部工具
    def _apply_update(
        self,
        table: str,
        id_column: str,
        id_value: str,
        changes: Mapping[str, Any],
    ) -> None:
        """通用局部更新：只更新 changes 中的键；CLEAR 哨兵置为 NULL。"""
        allowed = _ALLOWED_COLUMNS[table]
        sets: list[str] = []
        params: list[Any] = []
        for column, value in changes.items():
            if column not in allowed:
                raise ValueError(f"不允许更新的列：{table}.{column}")
            if value is CLEAR:
                sets.append(f"{column} = NULL")
            else:
                sets.append(f"{column} = ?")
                params.append(value)
        if not sets:
            return
        params.append(id_value)
        sql = f"UPDATE {table} SET {', '.join(sets)} WHERE {id_column} = ?"
        self.conn.execute(sql, params)


def build_repository(path: str = ":memory:") -> LedgerRepository:
    """便捷构造：连接 + 建表 + 返回仓储。"""
    conn = init_db(path)
    return LedgerRepository(conn)
