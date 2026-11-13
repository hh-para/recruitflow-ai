"""T09 端到端业务执行评测。

从空库开始，把人工标注的结构化事件注入真实 Harness 与存储层，核对最终状态。
此处仅验证“业务执行”链路，不验证模型自然语言理解（真实 AI 端到端评测另列，当前阻塞）。
"""
import pytest

from adapters.mock_sync import MockSyncAdapter
from harness.demo_events import HUMAN_DELIVERIES, build_delivery
from harness.processor import DeliveryHarness
from harness.syncer import run_due_tasks
from storage import LedgerRepository, init_db


def _find_app(repo, candidate_id, position):
    for a in repo.list_applications():
        if a["candidate_id"] == candidate_id and a["position"] == position:
            return a
    return None


def test_e2e_business_replay(tmp_path):
    conn = init_db(":memory:")
    repo = LedgerRepository(conn)
    harness = DeliveryHarness(repo)  # 无适配器，走 process_with_event（真实 Harness+存储）

    statuses = []
    for entry in HUMAN_DELIVERIES:
        outcome = harness.process_with_event(build_delivery(entry), entry["event"])
        statuses.append(outcome.status)

    # 重复投递 D012 应为 duplicate
    assert statuses[11] == "duplicate"

    # 最终数量
    candidates = repo.list_candidates()
    applications = repo.list_applications()
    interviews = repo.list_interviews()
    events = repo.list_business_events()
    reviews = repo.list_reviews()

    assert len(candidates) == 6
    assert len(applications) == 7
    assert len(interviews) == 3
    assert len(events) == 17
    assert len(repo.list_sync_tasks()) == 17
    assert len([r for r in reviews if r["status"] == "pending"]) == 0
    assert len([r for r in reviews if r["status"] == "resolved"]) == 2

    # 目标记录字段
    a1 = _find_app(repo, "C001", "后端开发")
    assert a1 is not None and a1["stage"] == "候选人退出" and a1["channel"] == "BOSS直聘"
    a2 = _find_app(repo, "C002", "产品经理")
    assert a2 is not None and a2["stage"] == "一面待进行"
    a3 = _find_app(repo, "C003", "数据分析师")
    assert a3 is not None and a3["stage"] == "待定"
    a7 = _find_app(repo, "C001", "数据工程师")
    assert a7 is not None and a7["stage"] == "新投递"

    iv_by_app = {}
    for iv in interviews:
        iv_by_app.setdefault(iv["application_id"], []).append(iv)

    iv_a1 = iv_by_app[a1["application_id"]][0]
    assert iv_a1["scheduled_at"] == "2026-11-18T14:00:00+08:00"
    assert iv_a1["interviewer"] == "陈峰"
    assert iv_a1["status"] == "已取消"

    iv_a3 = iv_by_app[a3["application_id"]][0]
    assert iv_a3["scheduled_at"] == "2026-11-17T14:00:00+08:00"
    assert iv_a3["interviewer"] == "孙磊"
    assert iv_a3["status"] == "已完成"
    assert iv_a3["result"] == "待定"

    iv_a2 = iv_by_app[a2["application_id"]][0]
    assert iv_a2["scheduled_at"] == "2026-11-18T10:00:00+08:00"
    assert iv_a2["status"] == "待进行"

    # 改期历史：M011 的 reschedule 记录前值/后值
    reschedule = [e for e in events if e["message_id"] == "M011" and e["action"] == "reschedule_interview"][0]
    assert reschedule["interview_before"]["scheduled_at"] == "2026-11-17T15:00:00+08:00"
    assert reschedule["interview_after"]["scheduled_at"] == "2026-11-18T14:00:00+08:00"

    # 更正历史：M016 引用 M015，阶段 一面通过 → 待定
    correct = [e for e in events if e["message_id"] == "M016"][0]
    assert correct["references_message_id"] == "M015"
    assert correct["application_changes"] == {"stage": "待定"}

    # 退出历史：M019 引用 M018，阶段 → 候选人退出，面试取消
    withdraw = [e for e in events if e["message_id"] == "M019"][0]
    assert withdraw["references_message_id"] == "M018"
    assert withdraw["application_changes"] == {"stage": "候选人退出"}

    # 模拟同步后，外部记录与本地最新状态一致
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"))
    run_due_tasks(repo, adapter)
    records = adapter.list_records()
    assert len(records) == 7
    local_by_app = {a["application_id"]: a for a in applications}
    for rec in records:
        local = local_by_app[rec["application_id"]]
        assert rec["stage"] == local["stage"]
        assert rec["candidate_id"] == local["candidate_id"]
        assert rec["position"] == local["position"]
