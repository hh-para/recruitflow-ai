"""T02 存储层测试：数据模型、唯一约束、局部更新、显式清空、事务回滚与持久化。"""
import pytest

from storage import CLEAR, DuplicateError, LedgerRepository, init_db


@pytest.fixture
def repo():
    """每个测试使用独立的空内存数据库。"""
    conn = init_db(":memory:")
    yield LedgerRepository(conn)
    conn.close()


def _seed_application(repo, application_id="A001", candidate_id="C001", position="后端开发"):
    repo.create_candidate(candidate_id, "王小明")
    return repo.create_application(
        application_id, candidate_id, position, "BOSS直聘", "林悦", "新投递"
    )


# ------------------------------------------------------------------ 唯一/外键

def test_two_same_name_candidates_independent(repo):
    repo.create_candidate("C003", "李明")
    repo.create_candidate("C004", "李明")

    assert repo.get_candidate("C003")["name"] == "李明"
    assert repo.get_candidate("C004")["name"] == "李明"
    assert len(repo.find_candidates_by_name("李明")) == 2
    # 两个同名候选人是独立记录，标识不同。
    assert repo.get_candidate("C003") != repo.get_candidate("C004")


def test_one_candidate_two_positions(repo):
    _seed_application(repo, "A001", "C001", "后端开发")
    repo.create_application("A007", "C001", "数据工程师", "BOSS直聘", "林悦", "新投递")

    apps = repo.list_applications()
    assert len(apps) == 2
    assert all(a["candidate_id"] == "C001" for a in apps)
    assert sorted(a["position"] for a in apps) == ["后端开发", "数据工程师"]
    # 候选人只有一名。
    assert len(repo.list_candidates()) == 1


def test_duplicate_application_same_candidate_position_rejected(repo):
    _seed_application(repo, "A001", "C001", "后端开发")
    with pytest.raises(DuplicateError):
        repo.create_application("A002", "C001", "后端开发", "猎聘", "周宁", "新投递")
    assert len(repo.list_applications()) == 1


def test_duplicate_message_unique_constraint(repo):
    args = dict(
        delivery_id="D001",
        source="mock_wecom",
        group_id="G_RECRUIT_001",
        message_id="M011",
        sent_at="2026-11-16T11:00:00+08:00",
        sender_id="HR001",
        sender_name="林悦",
        text="C001后端开发一面改到11月18日下午2点",
    )
    repo.insert_raw_message(**args)
    assert repo.message_exists("mock_wecom", "G_RECRUIT_001", "M011") is True

    with pytest.raises(DuplicateError):
        repo.insert_raw_message(**{**args, "delivery_id": "D012"})

    assert len(repo.list_raw_messages()) == 1


# ------------------------------------------------------------------ 局部更新

def test_partial_update_preserves_unprovided_fields(repo):
    _seed_application(repo)

    updated = repo.update_application("A001", {"stage": "初筛通过"})

    assert updated["stage"] == "初筛通过"
    # 未提供的字段保持原值。
    assert updated["channel"] == "BOSS直聘"
    assert updated["hr_owner"] == "林悦"
    assert updated["position"] == "后端开发"


def test_explicit_clear_nullable_field(repo):
    _seed_application(repo)
    repo.create_interview(
        "I001", "A001", 1, "2026-11-17T15:00:00+08:00", "陈峰", "已完成", "通过"
    )
    assert repo.get_interview("I001")["result"] == "通过"

    updated = repo.update_interview("I001", {"result": CLEAR})

    assert updated["result"] is None
    # 其余字段不受影响。
    assert updated["interviewer"] == "陈峰"
    assert updated["status"] == "已完成"


def test_clear_not_null_column_raises(repo):
    _seed_application(repo)
    # channel 为 NOT NULL，显式清空应被 SQLite 拒绝。
    with pytest.raises(Exception):
        repo.update_application("A001", {"channel": CLEAR})


# ------------------------------------------------------------------ 事务

def test_transaction_rollback_all(repo):
    _seed_application(repo)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with repo.transaction():
            event = repo.insert_business_event(
                message_id="M007",
                action="update_application",
                application_id="A001",
                application_changes={"stage": "初筛通过"},
            )
            repo.update_application("A001", {"stage": "初筛通过"})
            repo.create_sync_task(
                task_id="S001",
                business_event_id=event["id"],
                target="mock_tencent_doc",
            )
            raise Boom()

    # 业务事件、台账更新、同步任务全部回滚。
    assert repo.list_business_events() == []
    assert repo.get_application("A001")["stage"] == "新投递"
    assert repo.list_sync_tasks() == []


def test_transaction_commit_persists(repo):
    _seed_application(repo)
    with repo.transaction():
        event = repo.insert_business_event(
            message_id="M007",
            action="update_application",
            application_id="A001",
            application_changes={"stage": "初筛通过"},
        )
        repo.update_application("A001", {"stage": "初筛通过"})
        repo.create_sync_task(
            task_id="S001", business_event_id=event["id"], target="mock_tencent_doc"
        )

    assert len(repo.list_business_events()) == 1
    assert repo.get_application("A001")["stage"] == "初筛通过"
    assert len(repo.list_sync_tasks()) == 1


# ------------------------------------------------------------------ 持久化

def test_data_persists_after_reopen(tmp_path):
    db_path = tmp_path / "recruitflow.db"

    conn = init_db(db_path)
    repo = LedgerRepository(conn)
    repo.create_candidate("C001", "王小明")
    repo.create_application("A001", "C001", "后端开发", "BOSS直聘", "林悦", "新投递")
    conn.close()

    conn2 = init_db(db_path)
    repo2 = LedgerRepository(conn2)
    assert repo2.get_candidate("C001")["name"] == "王小明"
    assert repo2.get_application("A001")["position"] == "后端开发"
    conn2.close()
