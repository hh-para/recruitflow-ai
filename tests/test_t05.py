"""T05 确认/更正/退出/改期/相对日期测试。

说明：
- 使用 FakeAdapter 注入结构化事件；FakeAdapter 不读取 expected 文件、
  不按固定消息 ID 返回业务结果（这里用“返回固定注入事件”的方式）。
- 测试仅以标准答案作为断言，不验证模型自然语言理解能力。
"""
import pytest

from adapters import FakeAdapter
from harness.processor import DeliveryHarness
from harness.schema import EventType, InterviewInfo, StructuredEvent
from storage import LedgerRepository, init_db


@pytest.fixture
def repo():
    conn = init_db(":memory:")
    yield LedgerRepository(conn)
    conn.close()


def _delivery(message_id, sent_at, text):
    return {
        "delivery_id": f"D{message_id[1:]}",
        "source": "mock_wecom",
        "group_id": "G_RECRUIT_001",
        "message_id": message_id,
        "sent_at": sent_at,
        "sender_id": "HR001",
        "sender_name": "林悦",
        "text": text,
    }


def _run(repo, delivery, event):
    """用返回固定事件的 FakeAdapter 处理一条消息。"""
    return DeliveryHarness(repo, FakeAdapter(responses=lambda m: event)).process_delivery(delivery)


def _seed_two_limings(repo):
    repo.create_candidate("C003", "李明")
    repo.create_candidate("C004", "李明")
    repo.create_application("A003", "C003", "数据分析师", "官网", "林悦", "新投递")
    repo.create_application("A004", "C004", "前端开发", "猎聘", "周宁", "新投递")


# ------------------------------------------------------------------ 1 同名歧义确认
def test_confirm_same_name_to_correct_candidate(repo):
    _seed_two_limings(repo)

    schedule = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_name="李明",
        interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
    )
    out1 = _run(repo, _delivery("M012", "2026-11-16T11:10:00+08:00", "李明明天下午2点一面"), schedule)
    assert out1.status == "review_created"
    review = repo.get_review("M012")
    assert review["reason"] == "candidate_ambiguous"
    assert set(review["possible_application_ids"]) == {"A003", "A004"}

    confirm = StructuredEvent(
        event_type=EventType.confirm,
        references_message_id="M012",
        candidate_id="C003",
        position="数据分析师",
    )
    out2 = _run(repo, _delivery("M013", "2026-11-16T11:15:00+08:00", "确认M012是C003"), confirm)
    assert out2.status == "applied"

    assert repo.get_review("M012")["status"] == "resolved"
    interviews = repo.list_interviews()
    assert len(interviews) == 1
    assert interviews[0]["application_id"] == "A003"
    assert interviews[0]["scheduled_at"] == "2026-11-17T14:00:00+08:00"
    assert interviews[0]["interviewer"] == "孙磊"
    # 另一个李明不受影响
    assert repo.find_interview("A004", 1) is None


# ------------------------------------------------------------------ 2 多岗位歧义只退出指定岗位
def test_confirm_withdraw_only_specified_position(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "一面待进行")
    repo.create_application("A007", "C001", "数据工程师", "BOSS直聘", "林悦", "新投递")
    repo.create_interview("I001", "A001", 1, "2026-11-18T14:00:00+08:00", "陈峰", "待进行", None)

    withdraw = StructuredEvent(event_type=EventType.withdraw, candidate_id="C001", cancel_interview=True)
    out1 = _run(repo, _delivery("M018", "2026-11-17T16:10:00+08:00", "C001面试取消"), withdraw)
    assert out1.status == "review_created"
    assert repo.get_review("M018")["reason"] == "application_ambiguous"

    confirm = StructuredEvent(
        event_type=EventType.confirm,
        references_message_id="M018",
        candidate_id="C001",
        position="后端开发",
    )
    out2 = _run(repo, _delivery("M019", "2026-11-17T16:15:00+08:00", "确认指后端开发"), confirm)
    assert out2.status == "applied"

    assert repo.get_application("A001")["stage"] == "候选人退出"
    iv = repo.get_interview("I001")
    assert iv["status"] == "已取消"
    assert iv["scheduled_at"] == "2026-11-18T14:00:00+08:00"  # 保留原时间
    assert iv["interviewer"] == "陈峰"  # 保留面试官
    # 另一岗位不受影响
    assert repo.get_application("A007")["stage"] == "新投递"


# ------------------------------------------------------------------ 3 更正保留历史
def test_correct_preserves_history(repo):
    repo.create_candidate("C003", "李明")
    repo.create_application("A003", "C003", "数据分析师", "官网", "林悦", "一面待进行")
    repo.create_interview("I003", "A003", 1, "2026-11-17T14:00:00+08:00", "孙磊", "已完成", "通过")

    passed = StructuredEvent(
        event_type=EventType.update_stage,
        candidate_id="C003",
        position="数据分析师",
        application_changes={"stage": "一面通过"},
        interview=InterviewInfo(round=1, result="通过", status="已完成"),
    )
    _run(repo, _delivery("M015", "2026-11-17T15:00:00+08:00", "一面通过"), passed)

    correct = StructuredEvent(
        event_type=EventType.correct,
        references_message_id="M015",
        candidate_id="C003",
        position="数据分析师",
        application_changes={"stage": "待定"},
        interview=InterviewInfo(round=1, result="待定"),
    )
    out = _run(repo, _delivery("M016", "2026-11-17T15:05:00+08:00", "更正为待定"), correct)
    assert out.status == "applied"

    assert repo.get_application("A003")["stage"] == "待定"
    assert repo.get_interview("I003")["result"] == "待定"
    events = repo.list_business_events()
    assert len(events) == 2  # 原事件 + 更正事件
    # 更正事件引用原消息
    assert events[-1]["references_message_id"] == "M015"
    assert events[-1]["application_changes"] == {"stage": "待定"}


# ------------------------------------------------------------------ 4 改期保留面试官不新增面试
def test_reschedule_preserves_interviewer(repo):
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "一面待进行")
    repo.create_interview("I001", "A001", 1, "2026-11-17T15:00:00+08:00", "陈峰", "待进行", None)

    reschedule = StructuredEvent(
        event_type=EventType.reschedule_interview,
        candidate_id="C001",
        position="后端开发",
        interview=InterviewInfo(round=1, scheduled_at_raw="11月18日下午2点"),
    )
    out = _run(repo, _delivery("M011", "2026-11-16T11:00:00+08:00", "改到11月18日下午2点"), reschedule)
    assert out.status == "applied"

    interviews = repo.list_interviews()
    assert len(interviews) == 1  # 不新增面试
    assert interviews[0]["scheduled_at"] == "2026-11-18T14:00:00+08:00"
    assert interviews[0]["interviewer"] == "陈峰"  # 保留面试官


# ------------------------------------------------------------------ 5 跨天确认仍按原消息解释“明天”
def test_cross_day_confirm_uses_original_date_base(repo):
    _seed_two_limings(repo)

    schedule = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_name="李明",
        interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
    )
    _run(repo, _delivery("M012", "2026-11-16T11:10:00+08:00", "李明明天下午2点一面"), schedule)

    confirm = StructuredEvent(
        event_type=EventType.confirm,
        references_message_id="M012",
        candidate_id="C003",
        position="数据分析师",
    )
    # 确认消息在次日到达，但“明天”仍按 M012 时间解释。
    _run(repo, _delivery("M013", "2026-11-17T09:00:00+08:00", "确认M012是C003"), confirm)

    iv = repo.find_interview("A003", 1)
    assert iv["scheduled_at"] == "2026-11-17T14:00:00+08:00"


# ------------------------------------------------------------------ 6 重复确认幂等
def test_duplicate_confirm_idempotent(repo):
    _seed_two_limings(repo)
    schedule = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_name="李明",
        interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
    )
    _run(repo, _delivery("M012", "2026-11-16T11:10:00+08:00", "李明明天下午2点一面"), schedule)

    confirm = StructuredEvent(
        event_type=EventType.confirm,
        references_message_id="M012",
        candidate_id="C003",
        position="数据分析师",
    )
    assert _run(repo, _delivery("M013", "2026-11-16T11:15:00+08:00", "确认1"), confirm).status == "applied"
    # 再次确认同一 review
    out2 = _run(repo, _delivery("M014", "2026-11-16T11:20:00+08:00", "确认2"), confirm)
    assert out2.status == "duplicate"
    assert len(repo.list_interviews()) == 1
    assert len(repo.list_business_events()) == 1


# ------------------------------------------------------------------ 7 无效引用不改变台账
def test_invalid_reference_no_change(repo):
    _seed_two_limings(repo)

    # 引用不存在
    confirm = StructuredEvent(
        event_type=EventType.confirm,
        references_message_id="M999",
        candidate_id="C003",
        position="数据分析师",
    )
    out = _run(repo, _delivery("M100", "2026-11-16T11:30:00+08:00", "确认不存在"), confirm)
    assert out.status == "invalid_reference"
    assert len(repo.list_interviews()) == 0
    assert len(repo.list_business_events()) == 0

    # 引用已解决的 review：先造一个已解决 review
    schedule = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_name="李明",
        interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
    )
    _run(repo, _delivery("M012", "2026-11-16T11:10:00+08:00", "李明明天下午2点一面"), schedule)
    confirm_ok = StructuredEvent(
        event_type=EventType.confirm, references_message_id="M012", candidate_id="C003", position="数据分析师"
    )
    _run(repo, _delivery("M013", "2026-11-16T11:15:00+08:00", "确认"), confirm_ok)

    again = _run(repo, _delivery("M101", "2026-11-16T11:40:00+08:00", "再次确认"), confirm_ok)
    assert again.status == "duplicate"
    assert len(repo.list_business_events()) == 1  # 不重复执行


# ------------------------------------------------------------------ 8 确认事务中途失败整体回滚
def test_confirm_transaction_rollback(repo):
    _seed_two_limings(repo)
    schedule = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_name="李明",
        interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
    )
    _run(repo, _delivery("M012", "2026-11-16T11:10:00+08:00", "李明明天下午2点一面"), schedule)

    confirm = StructuredEvent(
        event_type=EventType.confirm,
        references_message_id="M012",
        candidate_id="C003",
        position="数据分析师",
    )

    def boom(**kwargs):
        raise RuntimeError("boom")

    # 在业务事件写入处注入失败
    repo.insert_business_event = boom

    harness = DeliveryHarness(repo, FakeAdapter(responses=lambda m: confirm))
    with pytest.raises(RuntimeError):
        harness.process_delivery(_delivery("M013", "2026-11-16T11:15:00+08:00", "确认"))

    # 整体回滚：review 仍待确认，无面试、无业务事件
    assert repo.get_review("M012")["status"] == "pending"
    assert len(repo.list_interviews()) == 0
    assert len(repo.list_business_events()) == 0
