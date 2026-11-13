"""真实模型适配器（基于官方 Anthropic SDK）。

配置（密钥仅从环境变量读取）：
- ``ANTHROPIC_API_KEY``：必填，API 密钥。
- ``ANTHROPIC_BASE_URL``：可选，自定义 API 地址（代理/兼容端点）。
- ``RECRUITFLOW_MODEL``：可选，模型名称，默认 ``claude-opus-5-5``。
- ``RECRUITFLOW_TIMEOUT``：可选，请求超时秒数，默认 60。

失败时抛出明确的 ``AdapterError`` 子类，绝不静默切换到 Fake 或离线结果。
日志与报错不打印密钥。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import anthropic
import pydantic

from harness.schema import StructuredEvent

from .base import ExtractionResult, MessageContext, ModelAdapter
from .errors import (
    AdapterAPIError,
    AdapterConfigError,
    AdapterInvalidJSONError,
    AdapterSchemaError,
    AdapterTimeoutError,
)
from .prompt import build_user_message, load_extract_prompt

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_TIMEOUT = 60.0
_MAX_TOKENS = 2048


class AnthropicAdapter(ModelAdapter):
    """使用 Anthropic Messages API 提取结构化招聘事件。"""

    name = "anthropic"

    def __init__(
        self,
        *,
        client: Any | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        system_prompt: str | None = None,
        max_tokens: int = _MAX_TOKENS,
    ):
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL") or None
        self.model = model or os.environ.get("RECRUITFLOW_MODEL") or DEFAULT_MODEL
        self.max_tokens = max_tokens

        try:
            self.timeout = (
                float(timeout)
                if timeout is not None
                else float(os.environ.get("RECRUITFLOW_TIMEOUT", DEFAULT_TIMEOUT))
            )
        except ValueError as exc:  # pragma: no cover - 防御性
            raise AdapterConfigError("RECRUITFLOW_TIMEOUT 必须是数字") from exc

        self.system_prompt = (
            system_prompt if system_prompt is not None else load_extract_prompt()
        )

        if client is not None:
            # 测试注入客户端；跳过密钥检查。
            self._client = client
        else:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise AdapterConfigError(
                    "未配置 ANTHROPIC_API_KEY（密钥仅从环境变量读取）"
                )
            kwargs: dict[str, Any] = {"api_key": api_key, "timeout": self.timeout}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = anthropic.Anthropic(**kwargs)

    def extract(self, message: MessageContext) -> ExtractionResult:
        start = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_prompt,
                messages=[
                    {"role": "user", "content": build_user_message(message)}
                ],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": self._json_schema(),
                    }
                },
            )
        except anthropic.APITimeoutError as exc:
            raise AdapterTimeoutError(f"模型调用超时（{self.timeout}s）") from exc
        except anthropic.AuthenticationError as exc:
            raise AdapterAPIError("模型 API 认证失败，请检查密钥") from exc
        except anthropic.APIStatusError as exc:
            raise AdapterAPIError(f"模型 API 错误（HTTP {exc.status_code}）") from exc
        except anthropic.APIConnectionError as exc:
            raise AdapterAPIError("模型 API 网络连接错误") from exc

        elapsed = time.perf_counter() - start

        if getattr(response, "stop_reason", None) == "refusal":
            raise AdapterAPIError("模型拒绝处理该请求")

        text = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            None,
        )
        if text is None:
            raise AdapterInvalidJSONError("模型响应中未找到文本输出")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AdapterInvalidJSONError(f"模型输出不是合法 JSON：{exc}") from exc

        try:
            event = StructuredEvent.model_validate(data)
        except pydantic.ValidationError as exc:
            raise AdapterSchemaError(f"模型输出不符合 Schema：{exc}") from exc

        model_name = getattr(response, "model", None) or self.model
        return ExtractionResult(
            event=event,
            model_name=model_name,
            elapsed_seconds=elapsed,
            usage=self._extract_usage(response),
        )

    def _json_schema(self) -> dict[str, Any]:
        schema = StructuredEvent.model_json_schema()
        schema.setdefault("additionalProperties", False)
        return schema

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        return {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            "cache_creation_input_tokens": getattr(
                usage, "cache_creation_input_tokens", None
            ),
        }
