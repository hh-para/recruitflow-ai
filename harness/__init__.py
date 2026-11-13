"""RecruitFlow 领域层：结构化事件 Schema 与消息处理 Harness。

注意：消息处理 Harness 位于 ``harness.processor``，请从该子模块导入
（``from harness.processor import DeliveryHarness``），避免在包初始化期
反向导入 adapters 造成循环依赖。
"""
from .schema import EventType, InterviewInfo, StructuredEvent

__all__ = ["EventType", "InterviewInfo", "StructuredEvent"]
