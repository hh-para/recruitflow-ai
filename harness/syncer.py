"""同步任务执行器（单 worker，复用 sync_tasks，不新增 Redis/Celery/常驻服务）。

设计选择：
- **读取最新状态（非快照）**：执行时从业务库读取该应聘记录的最新台账与面试，
  写入外部表。单 worker 串行执行保证同一应聘记录不会并发写，配合 upsert 幂等键
  避免重复/旧数据覆盖新数据。
- 外部调用期间不持有业务数据库写事务。
- 领取任务时标记 processing；processing 超时（默认 300s）后可被恢复重跑，
  外部写入成功但本地标记成功前崩溃时，重试按幂等键 upsert 不产生重复。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from adapters.mock_sync import PermanentSyncError, SyncAdapter, TemporarySyncError

TZ_SHANGHAI = timezone(timedelta(hours=8))
PROCESSING_TIMEOUT_SECONDS = 300

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_RETRY_WAIT = "retry_wait"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


def _now_iso(now: datetime) -> str:
    return now.astimezone(TZ_SHANGHAI).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_SHANGHAI)
    return dt.astimezone(TZ_SHANGHAI)


def backoff_delay(attempts: int, base: float = 1.0, max_delay: float = 60.0) -> float:
    """有上限的指数退避（确定性，便于注入时钟测试）。"""
    return min(base * (2 ** (attempts - 1)), max_delay)


def build_sync_record(repo, application_id: str) -> dict[str, Any] | None:
    app = repo.get_application(application_id)
    if app is None:
        return None
    interviews = [
        {
            "round": iv["round"],
            "scheduled_at": iv["scheduled_at"],
            "interviewer": iv["interviewer"],
            "status": iv["status"],
            "result": iv["result"],
        }
        for iv in repo.list_interviews()
        if iv["application_id"] == application_id
    ]
    return {
        "application_id": app["application_id"],
        "candidate_id": app["candidate_id"],
        "position": app["position"],
        "channel": app["channel"],
        "hr_owner": app["hr_owner"],
        "stage": app["stage"],
        "interviews": interviews,
    }


def _due_tasks(repo, now: datetime) -> list[dict[str, Any]]:
    due = []
    for t in repo.list_sync_tasks():
        if t["status"] == STATUS_PENDING:
            due.append(t)
        elif t["status"] == STATUS_RETRY_WAIT:
            nra = _parse_dt(t.get("next_retry_at"))
            if nra is not None and nra <= now:
                due.append(t)
        elif t["status"] == STATUS_PROCESSING:
            psa = _parse_dt(t.get("processing_started_at"))
            if psa is not None and (now - psa).total_seconds() > PROCESSING_TIMEOUT_SECONDS:
                due.append(t)
    # 同一应聘记录按顺序串行（按任务创建顺序），避免旧数据覆盖新数据
    due.sort(key=lambda t: (t.get("application_id") or "", t.get("id") or 0))
    return due


def run_due_tasks(repo, adapter: SyncAdapter, now: datetime | None = None, max_attempts: int = 3) -> list[dict[str, Any]]:
    now = (now or datetime.now(TZ_SHANGHAI)).astimezone(TZ_SHANGHAI)
    results = []
    for task in _due_tasks(repo, now):
        results.append(_execute_one(repo, adapter, task, now, max_attempts))
    return results


def _execute_one(repo, adapter, task, now, max_attempts) -> dict[str, Any]:
    task_id = task["task_id"]
    application_id = task.get("application_id")
    if not application_id:
        repo.update_sync_task(task_id, {"status": STATUS_FAILED, "last_error": "任务缺少 application_id"})
        return {"task_id": task_id, "status": STATUS_FAILED}

    attempts = (task.get("attempts") or 0) + 1
    # 领取：标记 processing，避免同一任务被重复执行；记录开始时间用于超时恢复。
    repo.update_sync_task(task_id, {
        "status": STATUS_PROCESSING,
        "processing_started_at": _now_iso(now),
        "attempts": attempts,
        "last_error": None,
    })

    record = build_sync_record(repo, application_id)
    if record is None:
        repo.update_sync_task(task_id, {"status": STATUS_FAILED, "last_error": "应聘记录不存在"})
        return {"task_id": task_id, "status": STATUS_FAILED}

    try:
        external_id = adapter.sync(record)  # 外部调用；不持有业务库写事务
    except TemporarySyncError as exc:
        if attempts >= max_attempts:
            repo.update_sync_task(task_id, {"status": STATUS_FAILED, "last_error": str(exc)})
            return {"task_id": task_id, "status": STATUS_FAILED}
        delay = backoff_delay(attempts)
        repo.update_sync_task(task_id, {
            "status": STATUS_RETRY_WAIT,
            "last_error": str(exc),
            "next_retry_at": _now_iso(now + timedelta(seconds=delay)),
        })
        return {"task_id": task_id, "status": STATUS_RETRY_WAIT}
    except PermanentSyncError as exc:
        repo.update_sync_task(task_id, {"status": STATUS_FAILED, "last_error": str(exc)})
        return {"task_id": task_id, "status": STATUS_FAILED}
    except Exception as exc:  # 未知错误按永久失败，可显式重试
        repo.update_sync_task(task_id, {"status": STATUS_FAILED, "last_error": str(exc)})
        return {"task_id": task_id, "status": STATUS_FAILED}

    repo.update_sync_task(task_id, {
        "status": STATUS_SUCCEEDED,
        "external_record_id": external_id,
        "completed_at": _now_iso(now),
        "last_error": None,
    })
    return {"task_id": task_id, "status": STATUS_SUCCEEDED}


def retry_failed(repo, task_id: str) -> dict[str, Any] | None:
    """把 failed 任务显式重置为 pending（用户主动重试）。"""
    task = repo.get_sync_task(task_id)
    if task is None:
        return None
    repo.update_sync_task(task_id, {
        "status": STATUS_PENDING,
        "next_retry_at": None,
        "last_error": None,
        "processing_started_at": None,
        "completed_at": None,
    })
    return repo.get_sync_task(task_id)
