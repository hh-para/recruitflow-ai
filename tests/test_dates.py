"""相对/绝对时间解析（harness.dates）测试。"""
from harness.dates import resolve_scheduled_at

BASE = "2026-11-16T09:00:00+08:00"


def test_tomorrow_afternoon():
    assert resolve_scheduled_at("明天下午3点", BASE) == "2026-11-17T15:00:00+08:00"


def test_tomorrow_afternoon_cn_numeral():
    assert resolve_scheduled_at("明天下午两点", BASE) == "2026-11-17T14:00:00+08:00"


def test_absolute_short_date():
    assert resolve_scheduled_at("11月18日上午10点", BASE) == "2026-11-18T10:00:00+08:00"


def test_absolute_full_date():
    assert resolve_scheduled_at("2026年11月20日下午两点", BASE) == "2026-11-20T14:00:00+08:00"


def test_ambiguous_week_returns_none():
    assert resolve_scheduled_at("下周", BASE) is None


def test_no_time_returns_none():
    assert resolve_scheduled_at("明天", BASE) is None
