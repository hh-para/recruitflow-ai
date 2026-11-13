"""智谱（GLM）真实模型适配器，OpenAI 兼容 chat/completions 端点。

配置（密钥仅从环境变量读取）：
- ``ZHIPU_API_KEY``：必填，API 密钥。
- ``ZHIPU_BASE_URL``：可选，默认 ``https://open.bigmodel.cn/api/paas/v4``。
- ``ZHIPU_MODEL``：可选，默认 ``glm-4.7-flash``。
- ``ZHIPU_TIMEOUT``：可选，默认 60。

仅用标准库（urllib）实现。失败抛出明确的 ``AdapterError`` 子类；API/限流异常
携带脱敏后的 error.code / error.message / request_id / Retry-After，不含密钥。
对限流（HTTP 429）做有上限退避重试：优先遵循 Retry-After，否则指数退避 + 随机
抖动，最多重试 ``max_retries`` 次（默认 3）。等待动作可注入，便于测试模拟。
"""
from __future__ import annotations

import json
import os
import random
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable

import pydantic

from harness.schema import StructuredEvent

from .base import ExtractionResult, MessageContext, ModelAdapter
from .errors import (
    AdapterAPIError,
    AdapterConfigError,
    AdapterInvalidJSONError,
    AdapterRateLimitError,
    AdapterSchemaError,
    AdapterTimeoutError,
)
from .prompt import build_user_message, load_extract_prompt

def _parse_json_content(content: str) -> Any:
    """解析模型输出中的 JSON，容忍 markdown 代码围栏。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _read_http_error(exc: urllib.error.HTTPError) -> dict[str, Any]:
    """从 HTTPError 中提取脱敏后的服务端信息（不含密钥）。"""
    request_id: str | None = None
    retry_after: float | None = None
    headers = getattr(exc, "headers", None)
    if headers is not None:
        request_id = headers.get("x-request-id") or headers.get("request-id")
        ra = headers.get("Retry-After") or headers.get("retry-after")
        if ra:
            try:
                retry_after = float(ra)
            except (TypeError, ValueError):
                retry_after = None

    error_code: Any = None
    error_message: str | None = None
    try:
        raw = exc.read().decode("utf-8", "replace")
        data = json.loads(raw)
        err = data.get("error", data)
        if isinstance(err, dict):
            error_code = err.get("code")
            error_message = err.get("message")
    except Exception:
        pass

    return {
        "status_code": exc.code,
        "error_code": error_code,
        "error_message": error_message,
        "request_id": request_id,
        "retry_after": retry_after,
    }


class ZhipuAdapter(ModelAdapter):
    """OpenAI 兼容 chat/completions 适配器（默认智谱 GLM 配置）。

    子类可通过覆盖类属性接入其它 OpenAI 兼容供应商（如 DeepSeek）。
    """

    name = "zhipu"
    provider_label = "智谱"
    ENV_API_KEY = "ZHIPU_API_KEY"
    ENV_BASE_URL = "ZHIPU_BASE_URL"
    ENV_MODEL = "ZHIPU_MODEL"
    ENV_TIMEOUT = "ZHIPU_TIMEOUT"
    DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
    DEFAULT_MODEL = "glm-4.7-flash"
    DEFAULT_TIMEOUT = 60.0

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        system_prompt: str | None = None,
        poster: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        max_retries: int = 3,
        sleeper: Callable[[float], None] | None = None,
        backoff_base: float = 1.0,
    ):
        self.base_url = (
            base_url or os.environ.get(self.ENV_BASE_URL) or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.environ.get(self.ENV_MODEL) or self.DEFAULT_MODEL
        self.system_prompt = (
            system_prompt if system_prompt is not None else load_extract_prompt()
        )
        try:
            self.timeout = (
                float(timeout)
                if timeout is not None
                else float(os.environ.get(self.ENV_TIMEOUT, self.DEFAULT_TIMEOUT))
            )
        except ValueError as exc:  # pragma: no cover - 防御性
            raise AdapterConfigError(f"{self.ENV_TIMEOUT} 必须是数字") from exc

        self.max_retries = max_retries
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self.backoff_base = backoff_base

        # poster 仅用于测试注入；真实调用走 _http_post。
        self._poster = poster
        self.api_key = os.environ.get(self.ENV_API_KEY)
        if poster is None and not self.api_key:
            raise AdapterConfigError(
                f"未配置 {self.ENV_API_KEY}（密钥仅从环境变量读取）"
            )

    def extract(self, message: MessageContext) -> ExtractionResult:
        start = time.perf_counter()
        payload = self._build_payload(message)
        data = self._post_with_retry(payload)
        elapsed = time.perf_counter() - start

        content = self._get_content(data)
        try:
            event_data = _parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise AdapterInvalidJSONError(f"模型输出不是合法 JSON：{exc}") from exc

        try:
            event = StructuredEvent.model_validate(event_data)
        except pydantic.ValidationError as exc:
            raise AdapterSchemaError(f"模型输出不符合 Schema：{exc}") from exc

        model_name = data.get("model") or self.model
        return ExtractionResult(
            event=event,
            model_name=model_name,
            elapsed_seconds=elapsed,
            usage=data.get("usage") or {},
        )

    def _build_payload(self, message: MessageContext) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": build_user_message(message)},
            ],
            "response_format": {"type": "json_object"},
        }

    def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送请求；仅对限流（429）做有上限退避重试。"""
        last: AdapterRateLimitError | None = None
        for attempt in range(self.max_retries + 1):  # 1 次初始 + 最多 max_retries 次重试
            try:
                return self._post(payload)
            except AdapterRateLimitError as exc:
                last = exc
                if attempt >= self.max_retries:
                    break
                self._sleep(self._backoff_delay(exc.retry_after, attempt))
        assert last is not None
        raise last

    def _backoff_delay(self, retry_after: float | None, attempt: int) -> float:
        if retry_after is not None and retry_after > 0:
            return float(retry_after)
        exp = self.backoff_base * (2 ** attempt)
        jitter = random.uniform(0.0, self.backoff_base)
        return exp + jitter

    def _sleep(self, delay: float) -> None:
        self._sleeper(delay)

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._poster is not None:
            return self._poster(payload)
        return self._http_post(payload)

    def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            info = _read_http_error(exc)
            msg = f"{self.provider_label} API 错误（HTTP {info['status_code']}）"
            if info["error_code"] is not None:
                msg += f" [code={info['error_code']}]"
            if info["error_message"]:
                msg += f"：{info['error_message']}"
            if info["status_code"] == 429:
                raise AdapterRateLimitError(
                    msg,
                    status_code=info["status_code"],
                    error_code=info["error_code"],
                    error_message=info["error_message"],
                    request_id=info["request_id"],
                    retry_after=info["retry_after"],
                ) from exc
            raise AdapterAPIError(
                msg,
                status_code=info["status_code"],
                error_code=info["error_code"],
                error_message=info["error_message"],
                request_id=info["request_id"],
                retry_after=info["retry_after"],
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise AdapterTimeoutError(f"{self.provider_label} 调用超时（{self.timeout}s）") from exc
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout)):
                raise AdapterTimeoutError(f"{self.provider_label} 调用超时（{self.timeout}s）") from exc
            raise AdapterAPIError(f"{self.provider_label} API 网络连接错误") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdapterAPIError(f"{self.provider_label} API 返回了无法解析的响应") from exc

    def _get_content(self, data: dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterAPIError(f"{self.provider_label} API 响应结构异常") from exc
