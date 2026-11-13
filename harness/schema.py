"""结构化招聘事件 Schema（Pydantic）。

这是模型提取层与 harness 之间的契约。模型只输出结构化事件，不生成 SQL、
内部应聘记录 ID 或数据库写入命令；是否歧义、是否需要人工确认由后续 harness
根据数据库上下文决定，模型只在 ``uncertainty`` 中说明不确定点。

字段语义：
- ``candidate_id`` / ``candidate_name`` / ``position``：身份与匹配线索（允许未提供）。
- ``application_changes``：应聘记录的局部变更（键为字段名，值为新值）；
  **未出现在其中的字段 = 不修改**。
- ``clear_fields``：明确清空（把字段名列进来），与“未提供”严格区分。
- ``interview``：面试信息；``suggested=True`` 表示“建议安排”，不等于已安排。
- ``cancel_interview``：退出/取消时置真，表示取消待进行面试。
- ``references_message_id``：确认/更正引用的原消息 ID。
- ``evidence``：原文证据片段；``uncertainty``：模型发现的不确定信息。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    """招聘事件类型（提取层，比 harness 的台账动作更细粒度）。"""

    create_application = "create_application"    # 新增应聘
    update_stage = "update_stage"                # 阶段变化（含否定/通过/待定）
    schedule_interview = "schedule_interview"    # 安排面试
    reschedule_interview = "reschedule_interview"  # 面试改期
    correct = "correct"                          # 更正（引用原消息）
    withdraw = "withdraw"                        # 退出
    confirm = "confirm"                          # 确认（引用原消息）
    irrelevant = "irrelevant"                    # 非招聘消息


class InterviewInfo(BaseModel):
    """面试相关信息；未提供的字段为 None。"""

    model_config = ConfigDict(extra="forbid")

    round: int | None = None
    scheduled_at_raw: str | None = None   # 时间原文，如“明天下午3点”
    scheduled_at: str | None = None       # 明确时间（ISO 8601，Asia/Shanghai）
    interviewer: str | None = None
    status: str | None = None             # 待进行 / 已完成 / 已取消
    result: str | None = None             # 通过 / 待定 / 未通过


class StructuredEvent(BaseModel):
    """一次结构化招聘事件。"""

    model_config = ConfigDict(extra="forbid")

    event_type: EventType

    # 身份与匹配线索（允许未提供）
    candidate_id: str | None = None       # 原文编号，如 C001
    candidate_name: str | None = None
    position: str | None = None

    # 应聘字段局部变更（未提供的字段不修改）
    application_changes: dict[str, Any] = Field(default_factory=dict)

    # 明确清空的字段（与“未提供”区分）
    clear_fields: list[str] = Field(default_factory=list)

    # 面试
    interview: InterviewInfo | None = None
    cancel_interview: bool = False
    suggested: bool = False               # 建议安排（不等于已安排）

    # 确认/更正引用原消息
    references_message_id: str | None = None

    # 证据与不确定信息
    evidence: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
