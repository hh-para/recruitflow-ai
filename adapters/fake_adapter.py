"""Fake 适配器：仅用于自动化测试。

不实现任何自然语言理解；按预设的映射或回调返回事件。不得用于生产，
也不得在真实模型失败时作为静默降级。
"""
from __future__ import annotations

from typing import Any, Callable

from harness.schema import StructuredEvent

from .base import ExtractionResult, MessageContext, ModelAdapter


class FakeAdapter(ModelAdapter):
    """按预设返回事件的测试适配器。"""

    name = "fake"

    def __init__(
        self,
        responses: dict[str, StructuredEvent] | Callable[[MessageContext], StructuredEvent] | None = None,
        model_name: str = "fake",
    ):
        self._responses = responses or {}
        self.model_name = model_name

    def extract(self, message: MessageContext) -> ExtractionResult:
        if callable(self._responses):
            event = self._responses(message)
        else:
            event = self._responses.get(message.text)
            if event is None:
                raise KeyError(
                    f"FakeAdapter 未配置消息 {message.text!r} 的预设响应"
                )
        if not isinstance(event, StructuredEvent):
            raise TypeError("FakeAdapter 预设响应必须是 StructuredEvent")
        return ExtractionResult(
            event=event,
            model_name=self.model_name,
            elapsed_seconds=0.0,
            usage={},
        )
