"""人工标注的演示/评测事件（非模型输出，非生产处理逻辑）。

这是 20 次投递对应的人工标注结构化事件，用于：
- 离线演示初始化（scripts/init_demo.py 通过 Harness 重放，不直接导入最终状态）。
- 业务执行评测（tests/test_e2e_business.py 注入真实 Harness 与存储层）。

注意：这里不按消息 ID 在 Harness 中硬编码处理结果；事件是独立的输入数据。
"""
from __future__ import annotations

from harness.schema import EventType, InterviewInfo, StructuredEvent

_SRC = "mock_wecom"
_GRP = "G_RECRUIT_001"

# 每条：delivery_id / message_id / sent_at / sender_id / sender_name / text / event
HUMAN_DELIVERIES: list[dict] = [
    {
        "delivery_id": "D001", "message_id": "M001", "sent_at": "2026-11-16T09:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "新增候选人C001王小明，应聘后端开发，BOSS直聘来的，我负责跟进。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C001", candidate_name="王小明",
            position="后端开发", application_changes={"channel": "BOSS直聘", "hr_owner": "林悦"},
        ),
    },
    {
        "delivery_id": "D002", "message_id": "M002", "sent_at": "2026-11-16T09:05:00+08:00",
        "sender_id": "HR002", "sender_name": "周宁",
        "text": "C002赵可欣，内推产品经理，由我跟进。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C002", candidate_name="赵可欣",
            position="产品经理", application_changes={"channel": "内推", "hr_owner": "周宁"},
        ),
    },
    {
        "delivery_id": "D003", "message_id": "M003", "sent_at": "2026-11-16T09:10:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C003李明，官网投递数据分析师，我来跟。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C003", candidate_name="李明",
            position="数据分析师", application_changes={"channel": "官网", "hr_owner": "林悦"},
        ),
    },
    {
        "delivery_id": "D004", "message_id": "M004", "sent_at": "2026-11-16T09:15:00+08:00",
        "sender_id": "HR002", "sender_name": "周宁",
        "text": "C004李明应聘前端开发，渠道猎聘，负责人周宁。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C004", candidate_name="李明",
            position="前端开发", application_changes={"channel": "猎聘", "hr_owner": "周宁"},
        ),
    },
    {
        "delivery_id": "D005", "message_id": "M005", "sent_at": "2026-11-16T09:20:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C005陈雨投递运营专员，来源BOSS直聘，我负责。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C005", candidate_name="陈雨",
            position="运营专员", application_changes={"channel": "BOSS直聘", "hr_owner": "林悦"},
        ),
    },
    {
        "delivery_id": "D006", "message_id": "M006", "sent_at": "2026-11-16T09:25:00+08:00",
        "sender_id": "HR002", "sender_name": "周宁",
        "text": "C006刘洋，内推测试工程师，由我负责。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C006", candidate_name="刘洋",
            position="测试工程师", application_changes={"channel": "内推", "hr_owner": "周宁"},
        ),
    },
    {
        "delivery_id": "D007", "message_id": "M007", "sent_at": "2026-11-16T10:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C001后端开发简历初筛通过。",
        "event": StructuredEvent(
            event_type=EventType.update_stage, candidate_id="C001", position="后端开发",
            application_changes={"stage": "初筛通过"},
        ),
    },
    {
        "delivery_id": "D008", "message_id": "M008", "sent_at": "2026-11-16T10:10:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C001后端开发一面定在明天下午3点，面试官陈峰。",
        "event": StructuredEvent(
            event_type=EventType.schedule_interview, candidate_id="C001", position="后端开发",
            interview=InterviewInfo(round=1, scheduled_at_raw="明天下午3点", interviewer="陈峰"),
        ),
    },
    {
        "delivery_id": "D009", "message_id": "M009", "sent_at": "2026-11-16T10:20:00+08:00",
        "sender_id": "HR002", "sender_name": "周宁",
        "text": "C002产品经理一面安排在11月18日上午10点，面试官何敏。",
        "event": StructuredEvent(
            event_type=EventType.schedule_interview, candidate_id="C002", position="产品经理",
            interview=InterviewInfo(round=1, scheduled_at_raw="11月18日上午10点", interviewer="何敏"),
        ),
    },
    {
        "delivery_id": "D010", "message_id": "M010", "sent_at": "2026-11-16T10:30:00+08:00",
        "sender_id": "HR002", "sender_name": "周宁",
        "text": "C006测试工程师初筛通过，建议下周安排一面，时间还没定。",
        "event": StructuredEvent(
            event_type=EventType.update_stage, candidate_id="C006", position="测试工程师",
            application_changes={"stage": "初筛通过"}, suggested=True,
        ),
    },
    {
        "delivery_id": "D011", "message_id": "M011", "sent_at": "2026-11-16T11:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C001后端开发一面改到11月18日下午2点，面试官不变。",
        "event": StructuredEvent(
            event_type=EventType.reschedule_interview, candidate_id="C001", position="后端开发",
            interview=InterviewInfo(round=1, scheduled_at_raw="11月18日下午2点"),
        ),
    },
    {
        "delivery_id": "D012", "message_id": "M011", "sent_at": "2026-11-16T11:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C001后端开发一面改到11月18日下午2点，面试官不变。",
        "event": StructuredEvent(
            event_type=EventType.reschedule_interview, candidate_id="C001", position="后端开发",
            interview=InterviewInfo(round=1, scheduled_at_raw="11月18日下午2点"),
        ),
    },
    {
        "delivery_id": "D013", "message_id": "M012", "sent_at": "2026-11-16T11:10:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "李明明天下午2点一面，面试官孙磊。",
        "event": StructuredEvent(
            event_type=EventType.schedule_interview, candidate_name="李明",
            interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
        ),
    },
    {
        "delivery_id": "D014", "message_id": "M013", "sent_at": "2026-11-16T11:15:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "确认M012说的是C003李明，数据分析师。",
        "event": StructuredEvent(
            event_type=EventType.confirm, references_message_id="M012",
            candidate_id="C003", position="数据分析师",
        ),
    },
    {
        "delivery_id": "D015", "message_id": "M014", "sent_at": "2026-11-16T14:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C005陈雨运营专员初筛没通过，暂不安排面试。",
        "event": StructuredEvent(
            event_type=EventType.update_stage, candidate_id="C005", position="运营专员",
            application_changes={"stage": "未通过"},
        ),
    },
    {
        "delivery_id": "D016", "message_id": "M015", "sent_at": "2026-11-17T15:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C003李明数据分析师一面通过，二面时间待定。",
        "event": StructuredEvent(
            event_type=EventType.update_stage, candidate_id="C003", position="数据分析师",
            application_changes={"stage": "一面通过"},
            interview=InterviewInfo(round=1, result="通过", status="已完成"),
        ),
    },
    {
        "delivery_id": "D017", "message_id": "M016", "sent_at": "2026-11-17T15:05:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "更正上一条，C003一面结果是待定，不是通过。",
        "event": StructuredEvent(
            event_type=EventType.correct, references_message_id="M015",
            candidate_id="C003", position="数据分析师",
            application_changes={"stage": "待定"},
            interview=InterviewInfo(round=1, result="待定"),
        ),
    },
    {
        "delivery_id": "D018", "message_id": "M017", "sent_at": "2026-11-17T16:00:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C001王小明也投了数据工程师，仍是BOSS直聘，由我负责，刚收到简历。",
        "event": StructuredEvent(
            event_type=EventType.create_application, candidate_id="C001", candidate_name="王小明",
            position="数据工程师", application_changes={"channel": "BOSS直聘", "hr_owner": "林悦"},
        ),
    },
    {
        "delivery_id": "D019", "message_id": "M018", "sent_at": "2026-11-17T16:10:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "C001的面试取消了，他不继续应聘这个岗位了。",
        "event": StructuredEvent(
            event_type=EventType.withdraw, candidate_id="C001", cancel_interview=True,
        ),
    },
    {
        "delivery_id": "D020", "message_id": "M019", "sent_at": "2026-11-17T16:15:00+08:00",
        "sender_id": "HR001", "sender_name": "林悦",
        "text": "确认M018指后端开发，数据工程师继续。",
        "event": StructuredEvent(
            event_type=EventType.confirm, references_message_id="M018",
            candidate_id="C001", position="后端开发",
        ),
    },
]


def build_delivery(entry: dict) -> dict:
    """从人工标注条目构造一条投递（复用 source/group）。"""
    return {
        "delivery_id": entry["delivery_id"],
        "source": _SRC,
        "group_id": _GRP,
        "message_id": entry["message_id"],
        "sent_at": entry["sent_at"],
        "sender_id": entry["sender_id"],
        "sender_name": entry["sender_name"],
        "text": entry["text"],
    }
