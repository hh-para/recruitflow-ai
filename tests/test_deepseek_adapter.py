"""DeepSeek 适配器测试（复用 OpenAI 兼容实现，注入 poster，不依赖真实网络）。"""
import json

import pytest

from adapters import DeepSeekAdapter, MessageContext
from adapters.errors import AdapterConfigError
from harness.schema import EventType


def _msg():
    return MessageContext(
        text="新增候选人C101周晓，应聘产品运营，来源内推，由林悦负责。",
        sent_at="2026-11-19T09:00:00+08:00",
    )


def _ok_poster(payload):
    assert payload["model"] == "deepseek-flash"
    assert payload["response_format"] == {"type": "json_object"}
    return {
        "model": "deepseek-flash",
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "event_type": "create_application",
                            "candidate_id": "C101",
                            "candidate_name": "周晓",
                            "position": "产品运营",
                            "application_changes": {"channel": "内推", "hr_owner": "林悦"},
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
    }


def test_deepseek_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    adapter = DeepSeekAdapter(poster=_ok_poster)
    assert adapter.name == "deepseek"
    assert adapter.model == "deepseek-flash"
    assert adapter.base_url == "https://api.deepseek.com"


def test_deepseek_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(AdapterConfigError):
        DeepSeekAdapter()


def test_deepseek_valid_output(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    adapter = DeepSeekAdapter(poster=_ok_poster)
    result = adapter.extract(_msg())
    assert result.event.event_type is EventType.create_application
    assert result.event.candidate_id == "C101"
    assert result.event.position == "产品运营"
    assert result.model_name == "deepseek-flash"
