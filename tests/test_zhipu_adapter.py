"""T03 智谱（GLM）适配器测试（使用模拟 poster，不依赖真实密钥或网络）。"""
import json
import socket
import urllib.error

import pytest

from adapters import MessageContext, ZhipuAdapter
from adapters.errors import (
    AdapterAPIError,
    AdapterConfigError,
    AdapterInvalidJSONError,
    AdapterRateLimitError,
    AdapterSchemaError,
    AdapterTimeoutError,
)

VALID_EVENT_DICT = {
    "event_type": "schedule_interview",
    "candidate_id": "C101",
    "candidate_name": "周晓",
    "position": "产品运营",
    "application_changes": {"channel": "内推", "hr_owner": "林悦", "stage": "一面待进行"},
    "interview": {
        "round": 1,
        "scheduled_at_raw": "2026年11月20日下午两点",
        "scheduled_at": "2026-11-20T14:00:00+08:00",
        "interviewer": "陈峰",
    },
    "evidence": ["2026年11月20日下午两点", "面试官陈峰"],
    "uncertainty": [],
}


def _msg():
    return MessageContext(
        text="新增候选人C101周晓，应聘产品运营，来源内推，由林悦负责，一面安排在2026年11月20日下午两点，面试官陈峰。",
        sent_at="2026-11-19T09:00:00+08:00",
        sender_name="林悦",
    )


def _ok_poster(payload):
    assert payload["model"] == "glm-4.7-flash"
    assert "response_format" in payload
    return {
        "model": "glm-4.7-flash",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": json.dumps(VALID_EVENT_DICT, ensure_ascii=False)}}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }


def test_zhipu_valid_output():
    adapter = ZhipuAdapter(poster=_ok_poster)
    result = adapter.extract(_msg())

    assert result.event.event_type.value == "schedule_interview"
    assert result.event.candidate_id == "C101"
    assert result.event.position == "产品运营"
    assert result.event.interview.interviewer == "陈峰"
    assert result.model_name == "glm-4.7-flash"
    assert result.usage["total_tokens"] == 140


def test_zhipu_invalid_json():
    def poster(payload):
        return {"choices": [{"message": {"content": "这不是JSON"}}]}

    adapter = ZhipuAdapter(poster=poster)
    with pytest.raises(AdapterInvalidJSONError):
        adapter.extract(_msg())


def test_zhipu_schema_mismatch():
    def poster(payload):
        return {"choices": [{"message": {"content": json.dumps({"event_type": "bad"})}}]}

    adapter = ZhipuAdapter(poster=poster)
    with pytest.raises(AdapterSchemaError):
        adapter.extract(_msg())


def test_zhipu_missing_key(monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(AdapterConfigError):
        ZhipuAdapter()


def test_zhipu_http_error(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "test-key")

    def raise_http(*a, **k):
        raise urllib.error.HTTPError(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            401,
            "Unauthorized",
            {},
            None,
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http)
    adapter = ZhipuAdapter()
    with pytest.raises(AdapterAPIError):
        adapter.extract(_msg())


def test_zhipu_timeout(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "test-key")

    def raise_timeout(*a, **k):
        raise socket.timeout("timed out")

    monkeypatch.setattr("urllib.request.urlopen", raise_timeout)
    adapter = ZhipuAdapter()
    with pytest.raises(AdapterTimeoutError):
        adapter.extract(_msg())


def _ok_response():
    return {
        "model": "glm-4.7-flash",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": json.dumps(VALID_EVENT_DICT, ensure_ascii=False)}}
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }


# ------------------------------------------------------------------ 限流重试
def test_zhipu_rate_limit_retry_with_backoff():
    calls = {"n": 0}
    sleeps = []

    def poster(payload):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise AdapterRateLimitError("限流", retry_after=None)
        return _ok_response()

    adapter = ZhipuAdapter(poster=poster, sleeper=sleeps.append, max_retries=3)
    result = adapter.extract(_msg())

    assert calls["n"] == 4  # 1 次初始 + 3 次重试
    assert len(sleeps) == 3
    # 指数退避单调递增（base=1.0 + 抖动，区间互不重叠）
    assert sleeps[0] < sleeps[1] < sleeps[2]
    assert result.event.candidate_id == "C101"


def test_zhipu_rate_limit_respects_retry_after():
    calls = {"n": 0}
    sleeps = []

    def poster(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AdapterRateLimitError("限流", retry_after=2.5)
        return _ok_response()

    adapter = ZhipuAdapter(poster=poster, sleeper=sleeps.append, max_retries=3)
    adapter.extract(_msg())

    assert calls["n"] == 2
    assert sleeps == [2.5]  # 优先遵循 Retry-After


def test_zhipu_rate_limit_exhausted():
    calls = {"n": 0}

    def poster(payload):
        calls["n"] += 1
        raise AdapterRateLimitError("限流", retry_after=None)

    adapter = ZhipuAdapter(poster=poster, sleeper=lambda d: None, max_retries=3)
    with pytest.raises(AdapterRateLimitError):
        adapter.extract(_msg())

    assert calls["n"] == 4  # 最多重试 3 次，不无限重试


def test_zhipu_non_rate_limit_not_retried():
    calls = {"n": 0}

    def poster(payload):
        calls["n"] += 1
        raise AdapterAPIError("认证失败", status_code=401)

    adapter = ZhipuAdapter(poster=poster, sleeper=lambda d: None, max_retries=3)
    with pytest.raises(AdapterAPIError):
        adapter.extract(_msg())

    assert calls["n"] == 1  # 非 429 不重试
