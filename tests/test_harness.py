"""T04 消息处理 Harness 测试（FakeAdapter 注入事件，不读标准答案、不按消息ID返回预设结果）。"""
import pytest

from adapters import FakeAdapter
from adapters.errors import AdapterAPIError
from harness.processor import DeliveryHarness
from harness.schema import EventType, InterviewInfo, StructuredEvent
from storage import LedgerRepository, init_db


@pytest.fixture
def repo():
    conn = init_db(":memory:")
    yield LedgerRepository(conn)
    conn.close()


def _delivery(message_id="M001", text="新增候选人C101周晓，应聘产品运营"):
    return {
        "delivery_id": "D001",
        "source": "mock_wecom",
        "group_id": "G_RECRUIT_001",
        "message_id": message_id,
        "sent_at": "2026-11-19T09:00:00+08:00",
        "sender_id": "HR001",
        "sender_name": "林悦",
        "text": text,
    }


def _create_app_event():
    return StructuredEvent(
        event_type=EventType.create_application,
        candidate_id="C101",
        candidate_name="周晓",
        position="产品运营",
        application_changes={"channel": "内推", "hr_owner": "林悦"},
    )


# ------------------------------------------------------------------ 唯一匹配
def test_create_application_unique_match(repo):
    harness = DeliveryHarness(repo, FakeAdapter({"新增候选人C101周晓，应聘产品运营": _create_app_event()}))
    out = harness.process_delivery(_delivery())

    assert out.status == "applied"
    assert repo.get_candidate("C101")["name"] == "周晓"
    apps = repo.list_applications()
    assert len(apps) == 1
    assert apps[0]["position"] == "产品运营"
    assert apps[0]["channel"] == "内推"
    assert apps[0]["hr_owner"] == "林悦"
    assert len(repo.list_business_events()) == 1
    assert len(repo.list_sync_tasks()) == 1
    raw = repo.get_raw_message("mock_wecom", "G_RECRUIT_001", "M001")
    assert raw["processed_at"] is not None


def test_update_stage_unique(repo):
    repo.create_candidate("C101", "周晓")
    repo.create_application("A001", "C101", "产品运营", "内推", "林悦", "新投递")
    event = StructuredEvent(
        event_type=EventType.update_stage,
        candidate_id="C101",
        position="产品运营",
        application_changes={"stage": "初筛通过"},
    )
    harness = DeliveryHarness(repo, FakeAdapter({"周晓初筛通过": event}))
    out = harness.process_delivery(_delivery(message_id="M003", text="周晓初筛通过"))

    assert out.status == "applied"
    assert repo.get_application("A001")["stage"] == "初筛通过"
    # 未提供字段保留
    assert repo.get_application("A001")["channel"] == "内推"
    assert len(repo.list_business_events()) == 1
    assert len(repo.list_sync_tasks()) == 1


def test_schedule_interview_unique(repo):
    repo.create_candidate("C101", "周晓")
    repo.create_application("A001", "C101", "产品运营", "内推", "林悦", "新投递")
    event = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_id="C101",
        position="产品运营",
        interview=InterviewInfo(round=1, scheduled_at="2026-11-20T14:00:00+08:00", interviewer="陈峰"),
    )
    harness = DeliveryHarness(repo, FakeAdapter({"周晓一面安排": event}))
    out = harness.process_delivery(_delivery(message_id="M004", text="周晓一面安排"))

    assert out.status == "applied"
    interviews = repo.list_interviews()
    assert len(interviews) == 1
    assert interviews[0]["scheduled_at"] == "2026-11-20T14:00:00+08:00"
    assert interviews[0]["interviewer"] == "陈峰"
    assert repo.get_application("A001")["stage"] == "一面待进行"
    assert len(repo.list_business_events()) == 1


# ------------------------------------------------------------------ 去重 / 幂等
def test_duplicate_delivery_no_reprocess(repo):
    harness = DeliveryHarness(repo, FakeAdapter({"新增候选人C101周晓，应聘产品运营": _create_app_event()}))
    delivery = _delivery()

    assert harness.process_delivery(delivery).status == "applied"
    assert harness.process_delivery(delivery).status == "duplicate"

    assert len(repo.list_business_events()) == 1
    assert len(repo.list_sync_tasks()) == 1


def test_reprocess_no_duplicate_updates(repo):
    harness = DeliveryHarness(repo, FakeAdapter({"新增候选人C101周晓，应聘产品运营": _create_app_event()}))
    delivery = _delivery()

    harness.process_delivery(delivery)
    harness.process_delivery(delivery)
    harness.process_delivery(delivery)

    assert len(repo.list_business_events()) == 1
    assert len(repo.list_sync_tasks()) == 1
    assert len(repo.list_applications()) == 1


# ------------------------------------------------------------------ 歧义 → 待确认
def test_ambiguity_creates_review(repo):
    repo.create_candidate("C003", "李明")
    repo.create_candidate("C004", "李明")
    repo.create_application("A003", "C003", "数据分析师", "官网", "林悦", "新投递")
    repo.create_application("A004", "C004", "前端开发", "猎聘", "周宁", "新投递")

    event = StructuredEvent(
        event_type=EventType.update_stage,
        candidate_name="李明",
        application_changes={"stage": "初筛通过"},
    )
    harness = DeliveryHarness(repo, FakeAdapter({"李明初筛通过": event}))
    out = harness.process_delivery(_delivery(message_id="M002", text="李明初筛通过"))

    assert out.status == "review_created"
    reviews = repo.list_reviews()
    assert len(reviews) == 1
    assert reviews[0]["reason"] == "candidate_ambiguous"
    assert set(reviews[0]["possible_application_ids"]) == {"A003", "A004"}
    # 未更新台账、未产生业务事件
    assert repo.get_application("A003")["stage"] == "新投递"
    assert len(repo.list_business_events()) == 0


# ------------------------------------------------------------------ 模型失败可重试
def test_model_failure_retryable(repo):
    def fail(message):
        raise AdapterAPIError("模型失败")

    delivery = _delivery()
    out1 = DeliveryHarness(repo, FakeAdapter(responses=fail)).process_delivery(delivery)
    assert out1.status == "failed"

    # 原始消息已保存，但未标记处理 → 可重试
    raw = repo.get_raw_message("mock_wecom", "G_RECRUIT_001", "M001")
    assert raw is not None
    assert raw["processed_at"] is None
    assert len(repo.list_business_events()) == 0

    working = FakeAdapter({"新增候选人C101周晓，应聘产品运营": _create_app_event()})
    out2 = DeliveryHarness(repo, working).process_delivery(delivery)
    assert out2.status == "applied"
    assert len(repo.list_business_events()) == 1


# ------------------------------------------------------------------ 事务边界
def test_no_write_tx_during_model_call(repo):
    observed = []

    def probe(message):
        observed.append(repo.in_transaction)
        return _create_app_event()

    harness = DeliveryHarness(repo, FakeAdapter(responses=probe))
    harness.process_delivery(_delivery())

    assert observed == [False]


# ------------------------------------------------------------------ 手动事件（跳过模型）
def test_process_with_event_manual(repo):
    event = _create_app_event()
    harness = DeliveryHarness(repo)  # 无适配器
    out = harness.process_with_event(_delivery(), event)

    assert out.status == "applied"
    assert repo.get_candidate("C101")["name"] == "周晓"
    assert len(repo.list_business_events()) == 1
    assert len(repo.list_sync_tasks()) == 1


def test_process_with_event_duplicate(repo):
    harness = DeliveryHarness(repo)
    delivery = _delivery()
    assert harness.process_with_event(delivery, _create_app_event()).status == "applied"
    # 同一消息再次处理 → 重复
    assert harness.process_with_event(delivery, _create_app_event()).status == "duplicate"
    assert len(repo.list_business_events()) == 1
