"""DeepSeek 官方 API 适配器（复用 OpenAI 兼容 chat/completions 实现）。

配置（密钥仅从环境变量读取）：
- ``DEEPSEEK_API_KEY``：必填，API 密钥。
- ``DEEPSEEK_BASE_URL``：可选，默认 ``https://api.deepseek.com``。
- ``DEEPSEEK_MODEL``：可选，默认 ``deepseek-flash``（非思考模型）。
- ``DEEPSEEK_TIMEOUT``：可选，默认 60。

复用现有提示词、Schema、Harness 与错误处理（继承 ``ZhipuAdapter`` 的
OpenAI 兼容实现）；招聘提取使用非思考模式 + ``response_format=json_object``，
本地 Pydantic 校验仍保留。
"""
from __future__ import annotations

from .zhipu_adapter import ZhipuAdapter


class DeepSeekAdapter(ZhipuAdapter):
    """DeepSeek 官方 API（OpenAI 兼容）适配器。"""

    name = "deepseek"
    provider_label = "DeepSeek"
    ENV_API_KEY = "DEEPSEEK_API_KEY"
    ENV_BASE_URL = "DEEPSEEK_BASE_URL"
    ENV_MODEL = "DEEPSEEK_MODEL"
    ENV_TIMEOUT = "DEEPSEEK_TIMEOUT"
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-flash"
    DEFAULT_TIMEOUT = 60.0
