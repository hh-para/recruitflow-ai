"""模型适配器异常类型。API 异常、超时、非法 JSON、Schema 错误明确区分。

API/限流异常会携带脱敏后的服务端信息（status_code、error.code、error.message、
request_id、Retry-After），但绝不携带密钥或 Authorization 请求头。
"""
from __future__ import annotations

from typing import Any


class AdapterError(Exception):
    """适配器基础异常。"""


class AdapterConfigError(AdapterError):
    """配置错误（如未配置 API 密钥、非法超时值）。"""


class AdapterAPIError(AdapterError):
    """模型 API 层错误（认证失败、HTTP 状态错误、网络连接错误）。

    携带脱敏后的服务端信息。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: Any = None,
        error_message: str | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_message = error_message
        self.request_id = request_id
        self.retry_after = retry_after


class AdapterRateLimitError(AdapterAPIError):
    """限流（HTTP 429）。``retry_after`` 为服务端建议等待秒数（可能为 None）。"""


class AdapterTimeoutError(AdapterAPIError):
    """模型调用超时。"""


class AdapterInvalidJSONError(AdapterError):
    """模型返回内容不是合法 JSON。"""


class AdapterSchemaError(AdapterError):
    """模型返回 JSON 合法但不符合 Schema。"""
