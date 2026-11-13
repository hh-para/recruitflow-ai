"""T07 招聘看板统计（harness.stats）测试。"""
from datetime import datetime, timedelta

import pytest

from harness.stats import TZ_SHANGHAI, compute_stats
from storage import LedgerRepository, init_db


@pytest.fixture
def repo():
    conn = init_db(":memory:")
    yield LedgerRepository(conn)
    conn.close()


def _now():
    return datetime.now(TZ_SHANGHAI)


def test_one_candidate_two_positions_counts(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "新投递")
    repo.create_application("A002", "C001", "数据工程师", "BOSS直聘", "林悦", "新投递")

    stats = compute_stats(repo, _now())
    assert stats.candidate_count == 1
    assert stats.application_count == 2
    assert stats.stage_distribution == {"新投递": 2}


def test_upcoming_interviews_only_pending(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "一面待进行")
    repo.create_application("A002", "C001", "数据工程师", "BOSS直聘", "林悦", "一面待进行")
    base = datetime(2026, 11, 19, 12, 0, tzinfo=TZ_SHANGHAI)

    repo.create_interview("I001", "A001", 1, "2026-11-20T10:00:00+08:00", "陈峰", "待进行", None)
    repo.create_interview("I002", "A002", 1, "2026-11-20T11:00:00+08:00", "何敏", "已完成", "通过")
    repo.create_interview("I003", "A002", 2, "2026-11-20T12:00:00+08:00", "孙磊", "已取消", None)

    stats = compute_stats(repo, base)
    assert [iv["interview_id"] for iv in stats.upcoming_interviews] == ["I001"]


def test_beijing_date_boundary(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "一面待进行")
    # 北京时间 11-16 23:30（UTC 15:30）应属“昨天”；11-17 00:30 应属“今天”。
    repo.create_interview("I001", "A001", 1, "2026-11-16T23:30:00+08:00", "陈峰", "待进行", None)
    repo.create_interview("I002", "A001", 2, "2026-11-17T00:30:00+08:00", "何敏", "待进行", None)

    base = datetime(2026, 11, 17, 12, 0, tzinfo=TZ_SHANGHAI)
    stats = compute_stats(repo, base)
    assert [iv["interview_id"] for iv in stats.upcoming_interviews] == ["I002"]


def test_stale_threshold_and_terminal_exclusion(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "新投递")
    repo.create_application("A002", "C001", "数据工程师", "BOSS直聘", "林悦", "未通过")
    repo.create_application("A003", "C001", "测试工程师", "BOSS直聘", "林悦", "候选人退出")

    base = _now() + timedelta(days=10)
    stats = compute_stats(repo, base, stale_days=7)
    assert {a["application_id"] for a in stats.stale_applications} == {"A001"}


def test_filter_consistency(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "初筛通过")
    repo.create_application("A002", "C001", "数据工程师", "BOSS直聘", "林悦", "新投递")

    stats = compute_stats(repo, _now(), position_filter="数据")
    assert stats.application_count == 1
    # 阶段分布与应聘记录数口径一致
    assert sum(stats.stage_distribution.values()) == 1
    # 候选人数为全局运维指标，不受筛选影响
    assert stats.candidate_count == 1


def test_dashboard_read_only(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "新投递")

    before = (len(repo.list_applications()), len(repo.list_business_events()), len(repo.list_sync_tasks()))
    compute_stats(repo, _now())
    after = (len(repo.list_applications()), len(repo.list_business_events()), len(repo.list_sync_tasks()))
    assert before == after


def test_empty_db(repo):
    stats = compute_stats(repo, _now())
    assert stats.candidate_count == 0
    assert stats.application_count == 0
    assert stats.open_review_count == 0
    assert stats.sync_pending_count == 0
    assert stats.sync_failed_count == 0
    assert stats.stage_distribution == {}
    assert stats.upcoming_interviews == []
    assert stats.stale_applications == []
