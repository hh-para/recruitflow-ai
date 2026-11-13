"""时间解析与校验（集中实现，避免散落在多个分支）。

- 相对日期以业务消息 ``sent_at`` 为基准，时区 Asia/Shanghai。
- 保留时间原文；无法确定时返回 None，不猜测具体时间。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

TZ_SHANGHAI = timezone(timedelta(hours=8))

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

_ABS_YEAR_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日?")
_ABS_SHORT_RE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日?")
_TIME_RE = re.compile(r"(上午|中午|下午|晚上)?\s*([零〇一二两三四五六七八九十0-9]+)\s*点(半)?")


def _cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if len(s) == 1:
        return _CN_DIGITS.get(s)
    if s.startswith("十"):
        return 10 + (_CN_DIGITS.get(s[1], 0) if len(s) > 1 else 0)
    if s.endswith("十"):
        return _CN_DIGITS.get(s[0], 0) * 10
    if "十" in s:
        tens, _, ones = s.partition("十")
        return _CN_DIGITS.get(tens, 0) * 10 + (_CN_DIGITS.get(ones, 0) if ones else 0)
    return None


def parse_base_sent_at(sent_at: str) -> datetime | None:
    """把 sent_at 解析为 Asia/Shanghai 时区的 datetime。"""
    try:
        dt = datetime.fromisoformat(sent_at)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_SHANGHAI)
    return dt.astimezone(TZ_SHANGHAI)


def resolve_scheduled_at(raw: str | None, base_sent_at: str) -> str | None:
    """把时间原文解析为带 Asia/Shanghai 偏移的 ISO 字符串；无法确定返回 None。"""
    if not raw:
        return None
    raw = raw.strip()
    base = parse_base_sent_at(base_sent_at)
    if base is None:
        return None

    # 日期
    day = None
    m = _ABS_YEAR_RE.search(raw)
    if m:
        day = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
    else:
        m = _ABS_SHORT_RE.search(raw)
        if m:
            day = datetime(base.year, int(m.group(1)), int(m.group(2))).date()
        elif "明天" in raw:
            day = (base + timedelta(days=1)).date()
        elif "后天" in raw:
            day = (base + timedelta(days=2)).date()
        elif "今天" in raw:
            day = base.date()

    # 时间
    hour = None
    minute = 0
    m = _TIME_RE.search(raw)
    if m:
        period, hour_str, half = m.group(1), m.group(2), m.group(3)
        hour = _cn_to_int(hour_str)
        if half:
            minute = 30
        if hour is not None and period in ("下午", "晚上") and hour < 12:
            hour += 12

    if day is None or hour is None:
        return None  # 不猜具体时间

    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ_SHANGHAI).isoformat()
