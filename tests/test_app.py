"""T06 Streamlit AppTest 测试。

说明：使用 AppTest 验证页面启动、提交后出现台账、重复提交不新增、待确认处理；
自动化测试不调用真实模型。手动结构化事件模式使用默认的 create_application 事件。
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from adapters import FakeAdapter
from harness.processor import DeliveryHarness
from harness.schema import EventType, InterviewInfo, StructuredEvent
from storage import LedgerRepository, init_db

APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")


def _find(elements, key):
    for el in elements:
        if getattr(el, "key", None) == key:
            return el
    return None


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    p = str(tmp_path / "test.db")
    monkeypatch.setenv("RECRUITFLOW_DB_PATH", p)
    monkeypatch.setenv("RECRUITFLOW_MOCK_SYNC_DB", str(tmp_path / "mock-sync.db"))
    return p


def test_app_starts(db_path):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not at.exception


def test_dashboard_empty_renders(db_path):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not at.exception
    # 空数据库：看板指标显示 0
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("候选人数") == "0"
    assert metrics.get("应聘记录数") == "0"
    assert metrics.get("未解决待确认") == "0"


def test_submit_creates_ledger_record(db_path):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not at.exception

    # 默认模式=手动；默认事件 JSON 即 create_application(C101 产品运营)。
    at.form_submit_button[0].click()
    at.run()
    assert not at.exception

    # 招聘台账页应出现 1 条记录
    assert len(at.dataframe) >= 1
    df = at.dataframe[0].value
    assert len(df) == 1
    assert df.iloc[0]["候选人编号"] == "C101"
    assert df.iloc[0]["岗位"] == "产品运营"


def test_duplicate_submit_no_new_record(db_path):
    at = AppTest.from_file(APP_PATH, default_timeout=30).run()

    # 固定消息ID，提交两次
    _find(at.text_input, "mid").input("M_TEST")
    at.form_submit_button[0].click()
    at.run()

    _find(at.text_input, "mid").input("M_TEST")
    at.form_submit_button[0].click()
    at.run()

    df = at.dataframe[0].value
    assert len(df) == 1  # 重复提交不新增台账记录


def test_review_confirm_updates_ledger(db_path):
    # 直接通过存储层与 Harness 预置同名歧义（不调用真实模型）。
    conn = init_db(db_path)
    repo = LedgerRepository(conn)
    repo.create_candidate("C003", "李明")
    repo.create_candidate("C004", "李明")
    repo.create_application("A003", "C003", "数据分析师", "官网", "林悦", "新投递")
    repo.create_application("A004", "C004", "前端开发", "猎聘", "周宁", "新投递")

    schedule = StructuredEvent(
        event_type=EventType.schedule_interview,
        candidate_name="李明",
        interview=InterviewInfo(round=1, scheduled_at_raw="明天下午2点", interviewer="孙磊"),
    )
    delivery = {
        "delivery_id": "D012",
        "source": "mock_wecom",
        "group_id": "G_RECRUIT_001",
        "message_id": "M012",
        "sent_at": "2026-11-16T11:10:00+08:00",
        "sender_id": "HR001",
        "sender_name": "林悦",
        "text": "李明明天下午2点一面",
    }
    DeliveryHarness(repo, FakeAdapter(responses=lambda m: schedule)).process_delivery(delivery)
    conn.close()

    at = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not at.exception

    # 待确认页应显示一条待确认，且有确认按钮
    confirm_btn = _find(at.button, "confirm_M012")
    assert confirm_btn is not None

    # 选择目标 A003（第一项），确认
    target = _find(at.selectbox, "target_M012")
    assert target is not None
    target.select_index(0)
    confirm_btn.click()
    at.run()

    assert not at.exception
    # 确认后：待确认列表清空、台账出现该应聘记录（阶段变为一面待进行）
    df = at.dataframe[0].value
    rows = df[df["候选人编号"] == "C003"]
    assert len(rows) == 1
    assert rows.iloc[0]["阶段"] == "一面待进行"
    assert "孙磊" in rows.iloc[0]["面试"]
