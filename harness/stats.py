"""招聘看板统计（纯计算：只读数据库，不写、不调用模型、不接外部平台）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

TZ_SHANGHAI = timezone(timedelta(hours=8))

# 终止状态：不再需要跟进（“已入职”为预留，当前阶段枚举中尚未出现）。
_TERMINAL_STAGES = {"未通过", "候选人退出", "已入职"}


def _parse_sqlite_utc(value: str | None) -> datetime | None:
    """解析 SQLite ``datetime('now')``（UTC）字符串，转 Asia/Shanghai。"""
    if not value:
        return None
    try:
        return (
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .astimezone(TZ_SHANGHAI)
        )
    except (ValueError, TypeError):
        return None


def _parse_iso(value: str | None) -> datetime | None:
    """解析带时区的 ISO 字符串，转 Asia/Shanghai。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_SHANGHAI)
    return dt.astimezone(TZ_SHANGHAI)


@dataclass
class DashboardStats:
    base_time: str
    candidate_count: int = 0
    application_count: int = 0
    open_review_count: int = 0
    sync_pending_count: int = 0
    sync_failed_count: int = 0
    stage_distribution: dict[str, int] = field(default_factory=dict)
    upcoming_interviews: list[dict[str, Any]] = field(default_factory=list)
    stale_applications: list[dict[str, Any]] = field(default_factory=list)


def compute_stats(
    repo,
    base_time: datetime,
    stale_days: int = 7,
    position_filter: str | None = None,
) -> DashboardStats:
    """按 ``base_time``（Asia/Shanghai）计算看板指标；只读，不修改数据库。

    ``position_filter`` 只影响“应聘记录”类指标（应聘数、阶段分布、待进行面试、
    超期跟进）；候选人数、待确认、同步任务等运维指标为全局统计，不受筛选影响。
    """
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=TZ_SHANGHAI)
    base_time = base_time.astimezone(TZ_SHANGHAI)

    all_applications = repo.list_applications()
    interviews = repo.list_interviews()
    sync_tasks = repo.list_sync_tasks()
    events = repo.list_business_events()

    applications = [
        a for a in all_applications
        if position_filter is None or position_filter in a["position"]
    ]
    filtered_ids = {a["application_id"] for a in applications}

    stats = DashboardStats(base_time=base_time.isoformat())

    # 全局运维指标（不受岗位筛选影响）
    stats.candidate_count = len({c["candidate_id"] for c in repo.list_candidates()})
    stats.open_review_count = len(repo.list_open_reviews())
    stats.sync_pending_count = sum(1 for t in sync_tasks if t["status"] == "pending")
    stats.sync_failed_count = sum(1 for t in sync_tasks if t["status"] == "failed")

    # 应聘记录类指标（受岗位筛选影响）
    stats.application_count = len(applications)

    dist: dict[str, int] = {}
    for a in applications:
        dist[a["stage"]] = dist.get(a["stage"], 0) + 1
    stats.stage_distribution = dict(sorted(dist.items()))

    today = base_time.date()
    upcoming = []
    for iv in interviews:
        if iv["application_id"] not in filtered_ids:
            continue
        if iv["status"] != "待进行":
            continue
        dt = _parse_iso(iv["scheduled_at"])
        if dt is None or dt.date() < today:
            continue
        upcoming.append(iv)
    upcoming.sort(key=lambda x: x["scheduled_at"])
    stats.upcoming_interviews = upcoming

    last_change_by_app: dict[str, datetime] = {}
    for e in events:
        app_id = e.get("application_id")
        if not app_id:
            continue
        ts = _parse_sqlite_utc(e.get("created_at"))
        if ts is None:
            continue
        if app_id not in last_change_by_app or ts > last_change_by_app[app_id]:
            last_change_by_app[app_id] = ts

    stale: list[dict[str, Any]] = []
    for a in applications:
        if a["stage"] in _TERMINAL_STAGES:
            continue
        last_change = last_change_by_app.get(a["application_id"])
        if last_change is None:
            last_change = _parse_sqlite_utc(a.get("created_at"))
        if last_change is None:
            continue
        if base_time - last_change > timedelta(days=stale_days):
            stale.append({**a, "last_change": last_change.isoformat()})
    stats.stale_applications = stale

    return stats
