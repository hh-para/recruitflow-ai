"""模型适配器统一接口。

输入：当前消息、发送元数据、显式传入的相关业务上下文。
输出：通过 Schema 校验的事件，以及调用耗时、模型名称和可获得的 token 用量。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from harness.schema import StructuredEvent


@dataclass
class MessageContext:
    """一次待提取的消息及其元数据与业务上下文。"""

    text: str
    sent_at: str
    message_id: str | None = None
    source: str | None = None
    group_id: str | None = None
    sender_id: str | None = None
    sender_name: str | None = None
    business_context: dict[str, Any] | None = None


@dataclass
class ExtractionResult:
    """一次成功提取的返回：Schema 校验后的事件 + 调用元数据。"""

    event: StructuredEvent
    model_name: str
    elapsed_seconds: float
    usage: dict[str, Any] = field(default_factory=dict)


class ModelAdapter(ABC):
    """统一模型适配器接口。"""

    name: str

    @abstractmethod
    def extract(self, message: MessageContext) -> ExtractionResult:
        """从消息提取结构化事件，返回校验后的事件与元数据。

        失败时抛出 :class:`adapters.errors.AdapterError` 的某个子类，
        不得静默降级为 Fake 或离线结果。
        """
        raise NotImplementedError
