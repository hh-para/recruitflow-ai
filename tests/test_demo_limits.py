"""公开演示服务端限制（DemoLimiter）测试。"""
from datetime import datetime, timedelta

from harness.demo_limits import TZ_SHANGHAI, DemoLimiter


def _now():
    return datetime(2026, 11, 20, 10, 0, tzinfo=TZ_SHANGHAI)


def test_message_length_limit(tmp_path):
    limiter = DemoLimiter(str(tmp_path / "l.db"), max_total=10, max_per_window=10)
    ok, reason = limiter.check_and_consume("x" * 501, 500, _now())
    assert not ok
    assert "过长" in reason
    # 长度拒绝不计数
    assert limiter.total_calls() == 0


def test_total_limit(tmp_path):
    limiter = DemoLimiter(str(tmp_path / "l.db"), max_total=2, max_per_window=100)
    now = _now()
    assert limiter.check_and_consume("a", 500, now)[0]
    assert limiter.check_and_consume("b", 500, now)[0]
    ok, reason = limiter.check_and_consume("c", 500, now)
    assert not ok
    assert "总调用" in reason
    assert limiter.total_calls() == 2


def test_rate_window_limit(tmp_path):
    limiter = DemoLimiter(str(tmp_path / "l.db"), max_total=100, max_per_window=2, window_seconds=3600)
    now = _now()
    assert limiter.check_and_consume("a", 500, now)[0]
    assert limiter.check_and_consume("b", 500, now)[0]
    ok, reason = limiter.check_and_consume("c", 500, now)
    assert not ok
    assert "频率" in reason
    # 窗口过后可再调用
    later = now + timedelta(seconds=3601)
    assert limiter.check_and_consume("d", 500, later)[0]


def test_consume_records(tmp_path):
    limiter = DemoLimiter(str(tmp_path / "l.db"))
    assert limiter.total_calls() == 0
    limiter.check_and_consume("a", 500, _now())
    assert limiter.total_calls() == 1
