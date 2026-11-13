"""T03 模型适配器测试（全部使用模拟响应，不依赖真实密钥或外部网络）。"""
import json

import anthropic
import httpx2
import pytest

from adapters import (
    AnthropicAdapter,
    FakeAdapter,
    MessageContext,
)
from adapters.errors import (
    AdapterAPIError,
    AdapterConfigError,
    AdapterInvalidJSONError,
    AdapterSchemaError,
    AdapterTimeoutError,
)
from harness.schema import EventType, StructuredEvent

VALID_EVENT_DICT = {
    "event_type": "schedule_interview",
    "candidate_id": "C001",
    "candidate_name": "王小明",
    "position": "后端开发",
    "application_changes": {"stage": "一面待进行"},
    "interview": {
        "round": 1,
        "scheduled_at_raw": "明天下午3点",
        "scheduled_at": "2026-11-17T15:00:00+08:00",
        "interviewer": "陈峰",
    },
    "evidence": ["明天下午3点", "面试官陈峰"],
    "uncertainty": [],
}


def _msg(text="C001后端开发一面定在明天下午3点，面试官陈峰。"):
    return MessageContext(
        text=text,
        sent_at="2026-11-16T10:10:00+08:00",
        message_id="M008",
        source="mock_wecom",
        group_id="G_RECRUIT_001",
        sender_id="HR001",
        sender_name="林悦",
    )


# ------------------------------------------------------------------ mock client
class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class _Response:
    def __init__(self, text, model="claude-opus-5-5", stop_reason="end_turn"):
        self.content = [_TextBlock(text)]
        self.model = model
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _Messages:
    def __init__(self, fn):
        self._fn = fn

    def create(self, **kwargs):
        return self._fn(**kwargs)


class _Client:
    def __init__(self, fn):
        self.messages = _Messages(fn)


def _req():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _make_adapter(fn):
    return AnthropicAdapter(client=_Client(fn))


# ------------------------------------------------------------------ Fake
def test_fake_adapter_returns_preset_event():
    preset = StructuredEvent(event_type="irrelevant")
    fake = FakeAdapter({"你好": preset})
    result = fake.extract(MessageContext(text="你好", sent_at="2026-11-16T09:00:00+08:00"))
    assert result.event is preset
    assert result.model_name == "fake"
    assert result.usage == {}


def test_fake_adapter_does_not_do_nlu():
    # Fake 适配器只按预设返回，不根据文本内容生成事件。
    fake = FakeAdapter({"你好": StructuredEvent(event_type="irrelevant")})
    result = fake.extract(MessageContext(text="你好", sent_at="2026-11-16T09:00:00+08:00"))
    assert result.event.event_type is EventType.irrelevant


# ------------------------------------------------------------------ real adapter
def test_anthropic_valid_output():
    captured = {}

    def ok(**kwargs):
        captured.update(kwargs)
        return _Response(json.dumps(VALID_EVENT_DICT, ensure_ascii=False))

    adapter = _make_adapter(ok)
    result = adapter.extract(_msg())

    assert result.event.event_type is EventType.schedule_interview
    assert result.event.candidate_id == "C001"
    assert result.event.interview.interviewer == "陈峰"
    assert result.model_name == "claude-opus-5-5"
    assert result.usage["input_tokens"] == 10
    assert result.elapsed_seconds >= 0
    # 请求确实带上了结构化输出配置与消息原文。
    assert captured["model"] == "claude-opus-5-5"
    assert "output_config" in captured


def test_anthropic_invalid_json():
    adapter = _make_adapter(lambda **kw: _Response("这不是JSON"))
    with pytest.raises(AdapterInvalidJSONError):
        adapter.extract(_msg())


def test_anthropic_schema_mismatch():
    bad = dict(VALID_EVENT_DICT)
    bad["event_type"] = "not_a_real_event"
    adapter = _make_adapter(lambda **kw: _Response(json.dumps(bad, ensure_ascii=False)))
    with pytest.raises(AdapterSchemaError):
        adapter.extract(_msg())


def test_anthropic_timeout():
    def timeout(**kw):
        raise anthropic.APITimeoutError(request=_req())

    adapter = _make_adapter(timeout)
    with pytest.raises(AdapterTimeoutError):
        adapter.extract(_msg())


def test_anthropic_api_error():
    def api_error(**kw):
        raise anthropic.APIStatusError(
            "boom", response=httpx2.Response(500, request=_req()), body=None
        )

    adapter = _make_adapter(api_error)
    with pytest.raises(AdapterAPIError):
        adapter.extract(_msg())


def test_anthropic_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AdapterConfigError):
        AnthropicAdapter()


def test_real_adapter_failure_does_not_fallback_to_fake():
    def fail(**kw):
        raise anthropic.APIStatusError(
            "boom", response=httpx2.Response(400, request=_req()), body=None
        )

    adapter = _make_adapter(fail)
    # 真实适配器失败时必须抛出 AdapterError，而不是静默返回 Fake 结果。
    with pytest.raises(AdapterAPIError):
        adapter.extract(_msg())

    # Fake 与真实适配器是彼此独立、明确区分的实现。
    assert AnthropicAdapter.name != FakeAdapter.name
