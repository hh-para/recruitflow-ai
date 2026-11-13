"""T08 模拟同步适配器与失败重试测试（注入时钟，不实际等待）。"""
from datetime import datetime, timedelta

import pytest

from adapters.mock_sync import MockSyncAdapter, PermanentSyncError
from harness.syncer import (
    PROCESSING_TIMEOUT_SECONDS,
    TZ_SHANGHAI,
    backoff_delay,
    run_due_tasks,
)
from storage import LedgerRepository, init_db


@pytest.fixture
def repo():
    conn = init_db(":memory:")
    yield LedgerRepository(conn)
    conn.close()


def _seed(repo, application_id="A001", stage="新投递", task_id="S1"):
    repo.create_candidate("C001", "王小明")
    repo.create_application(application_id, "C001", "后端开发", "BOSS直聘", "林悦", stage)
    repo.create_sync_task(
        task_id=task_id, business_event_id=None, target="mock_tencent_doc", application_id=application_id
    )


def _now():
    return datetime(2026, 11, 20, 10, 0, tzinfo=TZ_SHANGHAI)


def test_upsert_no_duplicate(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"))
    _seed(repo, "A001", "新投递", "S1")
    run_due_tasks(repo, adapter, now=_now())
    assert len(adapter.list_records()) == 1

    # 更新 + 新任务 → 不重复新增外部记录
    repo.update_application("A001", {"stage": "初筛通过"})
    repo.create_sync_task(task_id="S2", business_event_id=None, target="mock", application_id="A001")
    run_due_tasks(repo, adapter, now=_now())

    records = adapter.list_records()
    assert len(records) == 1
    assert records[0]["stage"] == "初筛通过"


def test_temp_failure_retry(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"), fail_first_n=1)
    _seed(repo)
    now = _now()

    r1 = run_due_tasks(repo, adapter, now=now)
    assert r1[0]["status"] == "retry_wait"
    task = repo.get_sync_task("S1")
    assert task["attempts"] == 1
    assert task["next_retry_at"] is not None

    later = now + timedelta(seconds=backoff_delay(1) + 1)
    r2 = run_due_tasks(repo, adapter, now=later)
    assert r2[0]["status"] == "succeeded"
    assert len(adapter.list_records()) == 1


def test_permanent_failure(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"), permanent_error=True)
    _seed(repo)
    r = run_due_tasks(repo, adapter, now=_now())
    assert r[0]["status"] == "failed"
    assert repo.get_sync_task("S1")["last_error"]


def test_retry_exhaustion(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"), fail_first_n=100)
    _seed(repo)
    now = _now()
    statuses = []
    for _ in range(3):
        r = run_due_tasks(repo, adapter, now=now)
        statuses.append(r[0]["status"])
        task = repo.get_sync_task("S1")
        if task["status"] == "retry_wait":
            now = datetime.fromisoformat(task["next_retry_at"]) + timedelta(seconds=1)
    assert statuses == ["retry_wait", "retry_wait", "failed"]
    assert repo.get_sync_task("S1")["attempts"] == 3


def test_future_retry_not_early(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"), fail_first_n=1)
    _seed(repo)
    now = _now()
    run_due_tasks(repo, adapter, now=now)  # 临时失败 → retry_wait
    # 未到期再跑，不执行
    assert run_due_tasks(repo, adapter, now=now) == []
    assert repo.get_sync_task("S1")["status"] == "retry_wait"


def test_crash_after_external_success_is_idempotent(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"))
    _seed(repo)

    orig = repo.update_sync_task

    def crash_on_success(task_id, changes):
        if changes.get("status") == "succeeded":
            raise RuntimeError("crash before local mark")
        return orig(task_id, changes)

    repo.update_sync_task = crash_on_success
    with pytest.raises(RuntimeError):
        run_due_tasks(repo, adapter, now=_now())

    # 外部已写 1 条，任务仍 processing
    assert repo.get_sync_task("S1")["status"] == "processing"
    assert len(adapter.list_records()) == 1

    # 恢复后（超过 processing 超时）重跑，幂等不重复
    repo.update_sync_task = orig
    later = _now() + timedelta(seconds=PROCESSING_TIMEOUT_SECONDS + 1)
    r = run_due_tasks(repo, adapter, now=later)
    assert r[0]["status"] == "succeeded"
    assert len(adapter.list_records()) == 1


def test_processing_recovery(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"))
    _seed(repo)
    # 模拟进程中断：任务停在 processing
    repo.update_sync_task("S1", {
        "status": "processing",
        "processing_started_at": "2026-11-20T10:00:00+08:00",
    })
    now = datetime(2026, 11, 20, 10, 10, tzinfo=TZ_SHANGHAI)  # 超过超时
    r = run_due_tasks(repo, adapter, now=now)
    assert r[0]["status"] == "succeeded"
    assert len(adapter.list_records()) == 1


def test_consecutive_updates_latest(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"))
    _seed(repo, "A001", "新投递", "S1")
    run_due_tasks(repo, adapter, now=_now())

    repo.update_application("A001", {"stage": "初筛通过"})
    repo.create_sync_task(task_id="S2", business_event_id=None, target="mock", application_id="A001")
    run_due_tasks(repo, adapter, now=_now())

    repo.update_application("A001", {"stage": "一面待进行"})
    repo.create_sync_task(task_id="S3", business_event_id=None, target="mock", application_id="A001")
    run_due_tasks(repo, adapter, now=_now())

    records = adapter.list_records()
    assert len(records) == 1
    assert records[0]["stage"] == "一面待进行"


def test_sync_failure_does_not_affect_ledger(repo, tmp_path):
    adapter = MockSyncAdapter(str(tmp_path / "mock.db"), permanent_error=True)
    _seed(repo, "A001", "新投递", "S1")
    before = repo.get_application("A001")
    run_due_tasks(repo, adapter, now=_now())
    after = repo.get_application("A001")
    assert before["stage"] == after["stage"]
    assert repo.get_sync_task("S1")["status"] == "failed"
