"""T03 结构化事件 Schema 测试。"""
import pytest
from pydantic import ValidationError

from harness.schema import EventType, InterviewInfo, StructuredEvent


def test_event_types_cover_all():
    values = {e.value for e in EventType}
    assert values == {
        "create_application",
        "update_stage",
        "schedule_interview",
        "reschedule_interview",
        "correct",
        "withdraw",
        "confirm",
        "irrelevant",
    }


def test_valid_event_parse():
    event = StructuredEvent(
        event_type="schedule_interview",
        candidate_id="C001",
        candidate_name="王小明",
        position="后端开发",
        application_changes={"stage": "一面待进行"},
        interview=InterviewInfo(
            round=1,
            scheduled_at_raw="明天下午3点",
            scheduled_at="2026-11-17T15:00:00+08:00",
            interviewer="陈峰",
        ),
        evidence=["明天下午3点", "面试官陈峰"],
    )
    assert event.event_type is EventType.schedule_interview
    assert event.candidate_id == "C001"
    assert event.interview.round == 1
    assert event.suggested is False


def test_unprovided_fields_default():
    event = StructuredEvent(event_type="irrelevant")
    assert event.candidate_id is None
    assert event.candidate_name is None
    assert event.position is None
    assert event.application_changes == {}
    assert event.clear_fields == []
    assert event.interview is None
    assert event.references_message_id is None


def test_unprovided_vs_explicit_clear():
    # 未提供：字段不在 application_changes、也不在 clear_fields。
    unprovided = StructuredEvent(event_type="update_stage", application_changes={"stage": "初筛通过"})
    assert "channel" not in unprovided.application_changes
    assert "channel" not in unprovided.clear_fields

    # 明确清空：字段名进入 clear_fields。
    cleared = StructuredEvent(event_type="update_stage", clear_fields=["channel"])
    assert "channel" in cleared.clear_fields


def test_reject_unknown_field():
    with pytest.raises(ValidationError):
        StructuredEvent.model_validate({"event_type": "irrelevant", "made_up_field": 1})


def test_reject_invalid_event_type():
    with pytest.raises(ValidationError):
        StructuredEvent(event_type="not_a_real_event")


def test_suggested_flag_distinguishes_suggestion():
    scheduled = StructuredEvent(event_type="schedule_interview")
    suggested = StructuredEvent(event_type="update_stage", suggested=True)
    assert scheduled.suggested is False
    assert suggested.suggested is True
    # 建议安排不能等价为已安排：suggested=True 的事件类型不应是 schedule_interview。
    assert suggested.event_type is not EventType.schedule_interview


def test_json_schema_is_strict():
    schema = StructuredEvent.model_json_schema()
    assert schema.get("additionalProperties") is False
