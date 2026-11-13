"""消息处理 Harness。

流程：去重 → 加载业务上下文 → 调用适配器提取并校验 → 匹配 → 事务写入或待确认。

关键约束：
- 模型网络调用期间**不持有数据库写事务**。
- 原始消息首次接收即保存（``processed_at`` 为空）；只有成功处理后才标记已处理，
  因此模型失败后允许重试，不会因“原始消息已保存”就把失败消息永久当作“已处理”。
- 去重键为 ``source + group_id + message_id``；已处理消息重复投递不产生重复业务更新或同步任务。
- 确认/更正/退出/改期与相对日期处理见各 handler；相对日期统一经 ``harness.dates`` 解析。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adapters import MessageContext, ModelAdapter
from adapters.errors import AdapterError
from harness.dates import resolve_scheduled_at
from harness.schema import EventType, StructuredEvent
from storage import CLEAR, LedgerRepository


@dataclass
class Outcome:
    """一次投递的处理结果。"""

    status: str  # applied / review_created / review_pending / duplicate / irrelevant / invalid_reference / failed
    business_event: dict | None = None
    review: dict | None = None
    error: str | None = None


@dataclass
class _Resolution:
    """候选人与应聘记录的定位结果。"""

    candidate_id: str | None = None
    application_id: str | None = None
    review_reason: str | None = None
    possible_application_ids: list[str] = field(default_factory=list)


class DeliveryHarness:
    """消息处理入口：串联去重、提取、匹配与事务写入。"""

    def __init__(
        self,
        repo: LedgerRepository,
        adapter: ModelAdapter | None = None,
        sync_target: str = "mock_tencent_doc",
    ):
        self.repo = repo
        self.adapter = adapter
        self.sync_target = sync_target

    # ------------------------------------------------------------------ 主流程
    def process_delivery(self, delivery: dict[str, Any]) -> Outcome:
        """接收消息并调用模型提取事件后处理。"""
        return self._process(delivery, event=None)

    def process_with_event(self, delivery: dict[str, Any], event: StructuredEvent) -> Outcome:
        """用调用方已校验的结构化事件处理一条消息（跳过模型调用）。"""
        return self._process(delivery, event=event)

    def _process(self, delivery: dict[str, Any], event: StructuredEvent | None) -> Outcome:
        source = delivery["source"]
        group_id = delivery["group_id"]
        message_id = delivery["message_id"]

        # 1) 去重：已处理过的消息直接跳过。
        existing = self.repo.get_raw_message(source, group_id, message_id)
        if existing is not None and existing.get("processed_at") is not None:
            return Outcome("duplicate")

        # 2) 保存原始消息（processed_at 为空；单条自动提交，不跨模型调用持有事务）。
        if existing is None:
            self.repo.insert_raw_message(
                delivery_id=delivery["delivery_id"],
                source=source,
                group_id=group_id,
                message_id=message_id,
                sent_at=delivery["sent_at"],
                sender_id=delivery.get("sender_id", ""),
                sender_name=delivery.get("sender_name", ""),
                text=delivery["text"],
            )

        # 3) 加载相关业务上下文（只读）。
        context = self._load_context()

        # 4) 获取结构化事件：模型提取或调用方提供。
        if event is None:
            if self.adapter is None:
                return Outcome("failed", error="未配置模型适配器")
            try:
                result = self.adapter.extract(
                    MessageContext(
                        text=delivery["text"],
                        sent_at=delivery["sent_at"],
                        message_id=message_id,
                        source=source,
                        group_id=group_id,
                        sender_id=delivery.get("sender_id"),
                        sender_name=delivery.get("sender_name"),
                        business_context=context,
                    )
                )
            except AdapterError as exc:
                return Outcome("failed", error=str(exc))
            event = result.event

        # 5) 同一事务：应用事件 + 标记已处理。
        with self.repo.transaction():
            outcome = self._apply_event(event, delivery)
            self.repo.mark_raw_message_processed(source, group_id, message_id)
            return outcome

    # ------------------------------------------------------------------ 上下文
    def _load_context(self) -> dict[str, Any]:
        return {
            "candidates": self.repo.list_candidates(),
            "applications": self.repo.list_applications(),
            "open_reviews": self.repo.list_open_reviews(),
        }

    # ------------------------------------------------------------------ 事件分发
    def _apply_event(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        et = event.event_type
        if et is EventType.irrelevant:
            return Outcome("irrelevant")
        if et is EventType.create_application:
            return self._handle_create_application(event, delivery)
        if et is EventType.update_stage:
            return self._handle_update_stage(event, delivery)
        if et is EventType.schedule_interview:
            return self._handle_schedule_interview(event, delivery)
        if et is EventType.reschedule_interview:
            return self._handle_reschedule_interview(event, delivery)
        if et is EventType.confirm:
            return self._handle_confirm(event, delivery)
        if et is EventType.correct:
            return self._handle_correct(event, delivery)
        if et is EventType.withdraw:
            return self._handle_withdraw(event, delivery)
        return Outcome("failed", error=f"事件类型 {et.value} 不受支持")

    # ------------------------------------------------------------------ 新增应聘
    def _handle_create_application(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        message_id = delivery["message_id"]
        candidate_id = event.candidate_id
        if candidate_id is None:
            if event.candidate_name:
                matches = self.repo.find_candidates_by_name(event.candidate_name)
                if len(matches) == 1:
                    candidate_id = matches[0]["candidate_id"]
                elif len(matches) > 1:
                    ids = {m["candidate_id"] for m in matches}
                    possible = [a["application_id"] for a in self.repo.list_applications() if a["candidate_id"] in ids]
                    return self._create_review(message_id, "candidate_ambiguous", possible, event, delivery)
                else:
                    candidate_id = self._next_id("C", self._candidate_ids())
                    self.repo.create_candidate(candidate_id, event.candidate_name)
            else:
                return self._create_review(message_id, "candidate_ambiguous", [], event, delivery)

        if not self.repo.get_candidate(candidate_id):
            self.repo.create_candidate(candidate_id, event.candidate_name or "")

        if not event.position:
            return self._create_review(message_id, "application_ambiguous", [], event, delivery)

        existing = self.repo.find_application(candidate_id, event.position)
        if existing:
            return self._create_review(message_id, "application_ambiguous", [existing["application_id"]], event, delivery)

        channel = event.application_changes.get("channel") or ""
        hr_owner = event.application_changes.get("hr_owner") or ""
        stage = event.application_changes.get("stage") or "新投递"
        application_id = self._next_id("A", self._application_ids())
        self.repo.create_application(application_id, candidate_id, event.position, channel, hr_owner, stage)

        event_record = self.repo.insert_business_event(
            message_id=message_id,
            action="create_application",
            application_id=application_id,
            application_changes={"candidate_id": candidate_id, "position": event.position, "channel": channel, "hr_owner": hr_owner, "stage": stage},
        )
        self._create_sync_task(event_record["id"], application_id)
        return Outcome("applied", business_event=event_record)

    # ------------------------------------------------------------------ 阶段变化
    def _handle_update_stage(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        res = self._locate_application(event)
        if res.review_reason:
            return self._create_review(delivery["message_id"], res.review_reason, res.possible_application_ids, event, delivery)
        return self._apply_update_stage(event, res.application_id, delivery["message_id"], None)

    # ------------------------------------------------------------------ 安排面试
    def _handle_schedule_interview(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        res = self._locate_application(event)
        if res.review_reason:
            return self._create_review(delivery["message_id"], res.review_reason, res.possible_application_ids, event, delivery)
        return self._apply_schedule_interview(event, res.application_id, delivery["message_id"], None, delivery["sent_at"])

    # ------------------------------------------------------------------ 面试改期
    def _handle_reschedule_interview(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        res = self._locate_application(event)
        if res.review_reason:
            return self._create_review(delivery["message_id"], res.review_reason, res.possible_application_ids, event, delivery)
        return self._apply_reschedule_interview(event, res.application_id, delivery["message_id"], None, delivery["sent_at"])

    # ------------------------------------------------------------------ 确认
    def _handle_confirm(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        ref_id = event.references_message_id
        if not ref_id:
            return Outcome("invalid_reference", error="确认消息缺少引用")

        review = self.repo.get_review(ref_id)
        if review is None:
            return Outcome("invalid_reference", error=f"确认引用不存在：{ref_id}")
        if review["status"] == "resolved":
            return Outcome("duplicate")  # 重复确认幂等
        if review.get("group_id") and review["group_id"] != delivery["group_id"]:
            return Outcome("invalid_reference", error="确认引用来自其他群组")

        pending = review.get("pending_event")
        if not pending:
            return Outcome("invalid_reference", error="待确认任务缺少原始事件")

        original = StructuredEvent.model_validate(pending)
        merged = self._merge_confirm(original, event)
        res = self._locate_application(merged)
        if res.review_reason:
            return Outcome("review_pending", review=review)  # 仍有歧义，保持待确认

        base_sent_at = review.get("pending_sent_at") or delivery["sent_at"]
        applied = self._apply_to_application(
            merged, res.application_id, delivery["message_id"],
            references_message_id=ref_id, base_sent_at=base_sent_at,
        )
        if applied.status != "applied":
            return applied  # 应用失败（如时间不明确），保持待确认
        self.repo.resolve_review(ref_id, delivery["message_id"])
        return applied

    # ------------------------------------------------------------------ 更正
    def _handle_correct(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        res = self._locate_application(event)
        if res.review_reason:
            return self._create_review(delivery["message_id"], res.review_reason, res.possible_application_ids, event, delivery)

        before = self._capture_application_before(res.application_id, event.application_changes, event.clear_fields)
        update_changes = dict(event.application_changes)
        for f in event.clear_fields:
            update_changes[f] = CLEAR
        if update_changes:
            self.repo.update_application(res.application_id, update_changes)

        interview_before, interview_after = self._apply_interview_changes(res.application_id, event.interview)

        event_record = self.repo.insert_business_event(
            message_id=delivery["message_id"],
            action="update_application",
            application_id=res.application_id,
            application_changes=dict(event.application_changes),
            application_before=before,
            interview_after=interview_after,
            interview_before=interview_before,
            references_message_id=event.references_message_id,
        )
        self._create_sync_task(event_record["id"], res.application_id)
        return Outcome("applied", business_event=event_record)

    # ------------------------------------------------------------------ 退出
    def _handle_withdraw(self, event: StructuredEvent, delivery: dict[str, Any]) -> Outcome:
        res = self._locate_application(event)
        if res.review_reason:
            return self._create_review(delivery["message_id"], res.review_reason, res.possible_application_ids, event, delivery)
        return self._apply_withdraw(event, res.application_id, delivery["message_id"], None)

    # ------------------------------------------------------------------ 定位与匹配
    def _locate_application(self, event: StructuredEvent) -> _Resolution:
        candidate_id, ambiguous = self._resolve_candidate(event)
        if len(ambiguous) > 1:
            possible = [a["application_id"] for a in self.repo.list_applications() if a["candidate_id"] in ambiguous]
            return _Resolution(review_reason="candidate_ambiguous", possible_application_ids=possible)
        if candidate_id is None:
            return _Resolution(review_reason="candidate_ambiguous", possible_application_ids=[])

        if event.position:
            app = self.repo.find_application(candidate_id, event.position)
            if app:
                return _Resolution(candidate_id=candidate_id, application_id=app["application_id"])
            return _Resolution(review_reason="application_ambiguous", possible_application_ids=[])

        apps = [a for a in self.repo.list_applications() if a["candidate_id"] == candidate_id]
        if len(apps) == 1:
            return _Resolution(candidate_id=candidate_id, application_id=apps[0]["application_id"])
        if len(apps) == 0:
            return _Resolution(review_reason="application_ambiguous", possible_application_ids=[])
        return _Resolution(review_reason="application_ambiguous", possible_application_ids=[a["application_id"] for a in apps])

    def _resolve_candidate(self, event: StructuredEvent) -> tuple[str | None, list[str]]:
        if event.candidate_id:
            if self.repo.get_candidate(event.candidate_id):
                return event.candidate_id, []
            return None, []
        if event.candidate_name:
            matches = self.repo.find_candidates_by_name(event.candidate_name)
            ids = [m["candidate_id"] for m in matches]
            if len(ids) == 1:
                return ids[0], []
            return None, ids
        return None, []

    # ------------------------------------------------------------------ 应用（已定位到应聘记录）
    def _apply_to_application(self, event, application_id, message_id, *, references_message_id=None, base_sent_at=None) -> Outcome:
        et = event.event_type
        if et is EventType.update_stage:
            return self._apply_update_stage(event, application_id, message_id, references_message_id)
        if et is EventType.schedule_interview:
            return self._apply_schedule_interview(event, application_id, message_id, references_message_id, base_sent_at)
        if et is EventType.reschedule_interview:
            return self._apply_reschedule_interview(event, application_id, message_id, references_message_id, base_sent_at)
        if et is EventType.withdraw:
            return self._apply_withdraw(event, application_id, message_id, references_message_id)
        return Outcome("failed", error=f"事件类型 {et.value} 无法在确认后应用")

    def _apply_update_stage(self, event, application_id, message_id, references_message_id) -> Outcome:
        before = self._capture_application_before(application_id, event.application_changes, event.clear_fields)
        update_changes = dict(event.application_changes)
        for f in event.clear_fields:
            update_changes[f] = CLEAR
        if update_changes:
            self.repo.update_application(application_id, update_changes)
        interview_before, interview_after = self._apply_interview_changes(application_id, event.interview)
        event_record = self.repo.insert_business_event(
            message_id=message_id, action="update_application", application_id=application_id,
            application_changes=dict(event.application_changes),
            application_before=before,
            interview_after=interview_after,
            interview_before=interview_before,
            references_message_id=references_message_id,
        )
        self._create_sync_task(event_record["id"], application_id)
        return Outcome("applied", business_event=event_record)

    def _apply_schedule_interview(self, event, application_id, message_id, references_message_id, base_sent_at) -> Outcome:
        iv = event.interview
        scheduled_at = self._resolve_interview_time(iv, base_sent_at)
        if scheduled_at is None:
            return Outcome("failed", error="时间不明确，未创建面试")
        round_no = (iv.round or 1) if iv else 1
        interview_id = self._next_id("I", self._interview_ids())
        self.repo.create_interview(interview_id, application_id, round_no, scheduled_at, (iv.interviewer or "") if iv else "", "待进行", None)
        before = self._capture_application_before(application_id, {"stage": "一面待进行"})
        self.repo.update_application(application_id, {"stage": "一面待进行"})
        event_record = self.repo.insert_business_event(
            message_id=message_id, action="schedule_interview", application_id=application_id,
            application_changes={"stage": "一面待进行"},
            application_before=before,
            interview_after=self.repo.get_interview(interview_id),
            references_message_id=references_message_id,
        )
        self._create_sync_task(event_record["id"], application_id)
        return Outcome("applied", business_event=event_record)

    def _apply_reschedule_interview(self, event, application_id, message_id, references_message_id, base_sent_at) -> Outcome:
        iv = event.interview
        round_no = (iv.round or 1) if iv else 1
        existing = self.repo.find_interview(application_id, round_no)
        if existing is None:
            return Outcome("failed", error="未找到可改期的面试")
        scheduled_at = self._resolve_interview_time(iv, base_sent_at)
        interview_before = {"scheduled_at": existing["scheduled_at"]}
        if iv and scheduled_at:
            self.repo.update_interview(existing["interview_id"], {"scheduled_at": scheduled_at})
        event_record = self.repo.insert_business_event(
            message_id=message_id, action="reschedule_interview", application_id=application_id,
            application_changes={},
            interview_after=self.repo.get_interview(existing["interview_id"]),
            interview_before=interview_before,
            references_message_id=references_message_id,
        )
        self._create_sync_task(event_record["id"], application_id)
        return Outcome("applied", business_event=event_record)

    def _apply_withdraw(self, event, application_id, message_id, references_message_id) -> Outcome:
        interview_after = None
        interview_before = None
        for iv in self.repo.list_interviews():
            if iv["application_id"] == application_id and iv["status"] == "待进行":
                interview_before = {"status": iv["status"]}
                self.repo.update_interview(iv["interview_id"], {"status": "已取消"})
                interview_after = self.repo.get_interview(iv["interview_id"])
        before = self._capture_application_before(application_id, {"stage": "候选人退出"})
        self.repo.update_application(application_id, {"stage": "候选人退出"})
        event_record = self.repo.insert_business_event(
            message_id=message_id, action="update_application", application_id=application_id,
            application_changes={"stage": "候选人退出"},
            application_before=before,
            interview_after=interview_after,
            interview_before=interview_before,
            references_message_id=references_message_id,
        )
        self._create_sync_task(event_record["id"], application_id)
        return Outcome("applied", business_event=event_record)

    # ------------------------------------------------------------------ 确认合并与时间解析
    def _merge_confirm(self, original: StructuredEvent, confirm: StructuredEvent) -> StructuredEvent:
        data = original.model_dump(mode="json")
        if confirm.candidate_id:
            data["candidate_id"] = confirm.candidate_id
        if confirm.candidate_name:
            data["candidate_name"] = confirm.candidate_name
        if confirm.position:
            data["position"] = confirm.position
        return StructuredEvent.model_validate(data)

    def _resolve_interview_time(self, interview, base_sent_at: str | None) -> str | None:
        if interview is None:
            return None
        if interview.scheduled_at_raw:
            if base_sent_at is None:
                return None
            return resolve_scheduled_at(interview.scheduled_at_raw, base_sent_at)
        return interview.scheduled_at

    # ------------------------------------------------------------------ 变更前值（用于变更历史）
    def _capture_application_before(self, application_id, changes, clear_fields=()):
        current = self.repo.get_application(application_id)
        before = {}
        if current:
            for field in list(changes.keys()) + list(clear_fields):
                if field in current:
                    before[field] = current[field]
        return before

    def _capture_interview_before(self, application_id, round_no, changes):
        existing = self.repo.find_interview(application_id, round_no)
        if not existing:
            return None
        return {field: existing.get(field) for field in changes if field in existing}

    def _apply_interview_changes(self, application_id, iv):
        """应用 event.interview 的结果/状态；返回 (before, after)。"""
        if iv is None:
            return None, None
        round_no = iv.round or 1
        existing = self.repo.find_interview(application_id, round_no)
        if existing is None:
            return None, None
        changes = {}
        if iv.result is not None:
            changes["result"] = iv.result
        if iv.status is not None:
            changes["status"] = iv.status
        before = None
        if changes:
            before = self._capture_interview_before(application_id, round_no, changes)
            self.repo.update_interview(existing["interview_id"], changes)
        return before, self.repo.get_interview(existing["interview_id"])

    # ------------------------------------------------------------------ 工具
    def _create_review(self, message_id, reason, possible_ids, pending_event, delivery) -> Outcome:
        review = self.repo.create_review(
            message_id=message_id,
            reason=reason,
            possible_application_ids=possible_ids,
            pending_event=pending_event.model_dump(mode="json"),
            pending_sent_at=delivery["sent_at"],
            group_id=delivery["group_id"],
        )
        return Outcome("review_created", review=review)

    def _create_sync_task(self, business_event_id: int, application_id: str | None) -> dict[str, Any]:
        task_id = self._next_id("S", self._sync_task_ids())
        return self.repo.create_sync_task(
            task_id=task_id,
            business_event_id=business_event_id,
            target=self.sync_target,
            application_id=application_id,
        )

    def _next_id(self, prefix: str, existing_ids: list[str]) -> str:
        nums = []
        for i in existing_ids:
            if isinstance(i, str) and i.startswith(prefix):
                try:
                    nums.append(int(i[len(prefix):]))
                except ValueError:
                    pass
        n = max(nums) + 1 if nums else 1
        return f"{prefix}{n:03d}"

    def _candidate_ids(self) -> list[str]:
        return [c["candidate_id"] for c in self.repo.list_candidates()]

    def _application_ids(self) -> list[str]:
        return [a["application_id"] for a in self.repo.list_applications()]

    def _interview_ids(self) -> list[str]:
        return [i["interview_id"] for i in self.repo.list_interviews()]

    def _sync_task_ids(self) -> list[str]:
        return [t["task_id"] for t in self.repo.list_sync_tasks()]
