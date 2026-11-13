"""模型适配器：统一接口 + 真实模型适配器 + 测试用 Fake 适配器。"""
from .base import ExtractionResult, MessageContext, ModelAdapter
from .errors import (
    AdapterError,
    AdapterAPIError,
    AdapterConfigError,
    AdapterInvalidJSONError,
    AdapterSchemaError,
    AdapterTimeoutError,
)
from .anthropic_adapter import AnthropicAdapter
from .deepseek_adapter import DeepSeekAdapter
from .fake_adapter import FakeAdapter
from .mock_sync import MockSyncAdapter, PermanentSyncError, SyncAdapter, SyncError, TemporarySyncError
from .zhipu_adapter import ZhipuAdapter

__all__ = [
    "ExtractionResult",
    "MessageContext",
    "ModelAdapter",
    "AdapterError",
    "AdapterAPIError",
    "AdapterConfigError",
    "AdapterInvalidJSONError",
    "AdapterSchemaError",
    "AdapterTimeoutError",
    "AnthropicAdapter",
    "DeepSeekAdapter",
    "FakeAdapter",
    "ZhipuAdapter",
    "SyncAdapter",
    "SyncError",
    "TemporarySyncError",
    "PermanentSyncError",
    "MockSyncAdapter",
]
