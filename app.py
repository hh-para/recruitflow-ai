"""RecruitFlow AI — Streamlit 界面。

复用 ``DeliveryHarness`` 与存储层，不在页面重复实现业务规则。

- 两种模式：真实模型模式 / 手动结构化事件模式。
- 三个页面：消息处理、招聘台账、待确认。
- 文件型 SQLite（路径经 RECRUITFLOW_DB_PATH 配置，默认 recruitflow-demo.db），
  每次 rerun 新建连接，不跨线程复用；旧库缺列由 storage.migrate_db 安全迁移。

启动：``python -m streamlit run app.py``
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pydantic
import streamlit as st

from adapters import DeepSeekAdapter
from adapters.errors import AdapterConfigError
from adapters.mock_sync import MockSyncAdapter
from harness.processor import DeliveryHarness, Outcome
from harness.schema import EventType, StructuredEvent
from harness.stats import compute_stats
from harness.syncer import retry_failed, run_due_tasks
from storage import LedgerRepository, init_db

TZ_SHANGHAI = timezone(timedelta(hours=8))

_EXAMPLE_EVENT = {
    "event_type": "create_application",
    "candidate_id": "C101",
    "candidate_name": "周晓",
    "position": "产品运营",
    "application_changes": {"channel": "内推", "hr_owner": "林悦"},
    "evidence": ["C101周晓", "产品运营", "内推"],
    "uncertainty": [],
}

_STATUS_LABELS = {
    "applied": "✅ 成功",
    "review_created": "⏳ 待确认（已创建待确认任务）",
    "review_pending": "⏳ 仍待确认（确认后目标仍不唯一）",
    "duplicate": "🔁 重复投递（未产生新变更）",
    "irrelevant": "ℹ️ 非招聘消息",
    "invalid_reference": "⚠️ 无效引用",
    "failed": "❌ 失败",
}


def _db_path() -> str:
    return os.environ.get(
        "RECRUITFLOW_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "recruitflow-demo.db"),
    )


def _now_shanghai() -> str:
    return datetime.now(TZ_SHANGHAI).isoformat()


def _process_message(repo: LedgerRepository, delivery: dict, event_json: str | None, mode: str) -> Outcome:
    harness = DeliveryHarness(repo)
    if mode == "手动结构化事件模式":
        try:
            data = json.loads(event_json)
        except json.JSONDecodeError as exc:
            return Outcome("failed", error=f"事件 JSON 不是合法 JSON：{exc}")
        try:
            event = StructuredEvent.model_validate(data)
        except pydantic.ValidationError as exc:
            return Outcome("failed", error=f"事件不符合 Schema：{exc}")
        return harness.process_with_event(delivery, event)

    # 真实模型模式（DeepSeek 官方 API，非思考 + JSON 输出，本地 Pydantic 校验）
    try:
        adapter = DeepSeekAdapter()
    except AdapterConfigError as exc:
        return Outcome("failed", error=f"未配置真实模型（{exc}），未自动切换模式")
    harness = DeliveryHarness(repo, adapter)
    return harness.process_delivery(delivery)


def _render_outcome(outcome: Outcome):
    st.write(f"**处理结果**：{_STATUS_LABELS.get(outcome.status, outcome.status)}")
    if outcome.error:
        st.error(outcome.error)
    if outcome.business_event:
        st.write("**关键变更**：")
        st.json(outcome.business_event.get("application_changes"))
    if outcome.review:
        st.write("**待确认**：")
        st.json({
            "message_id": outcome.review["message_id"],
            "reason": outcome.review["reason"],
            "possible_application_ids": outcome.review["possible_application_ids"],
        })


def _change_history_rows(repo: LedgerRepository, application_id: str) -> list[dict]:
    """按应聘记录构建变更历史行（业务时间/处理时间/来源/原文/变更前后/引用）。"""
    events = [e for e in repo.list_business_events() if e.get("application_id") == application_id]
    raw_by_id = {m["message_id"]: m for m in repo.list_raw_messages()}
    review_ids = {r["message_id"] for r in repo.list_reviews()}

    rows = []
    for e in events:
        ref = e.get("references_message_id")
        # 确认（引用 review 原消息）的业务发生时间依据原消息；其余依据自身消息。
        biz_msg = None
        if ref and ref in review_ids:
            biz_msg = raw_by_id.get(ref)
        if biz_msg is None:
            biz_msg = raw_by_id.get(e["message_id"])

        changes = []
        for k, v in (e.get("application_changes") or {}).items():
            before = (e.get("application_before") or {}).get(k)
            changes.append(f"{k}: {before} → {v}")
        for k, before in (e.get("interview_before") or {}).items():
            after = (e.get("interview_after") or {}).get(k)
            changes.append(f"面试{k}: {before} → {after}")

        rows.append({
            "业务时间": biz_msg["sent_at"] if biz_msg else None,
            "处理时间": e.get("created_at"),
            "来源消息": e["message_id"],
            "原文": biz_msg["text"] if biz_msg else None,
            "事件类型": e["action"],
            "变更前后": "；".join(changes) or "—",
            "引用原消息": ref,
        })
    return rows


st.set_page_config(page_title="RecruitFlow AI", layout="wide")

# 每次 rerun 新建连接，避免跨线程复用不安全的 SQLite 连接。
conn = init_db(_db_path())
repo = LedgerRepository(conn)

st.title("RecruitFlow AI — 招聘数据记录 Demo")
st.caption("企微消息入口为模拟；腾讯文档未接入（同步任务仅落库，显示“待同步”）。")

if os.path.exists(_db_path() + ".demo"):
    st.info("人工标注事件演示（离线，通过 Harness 重放人工标注事件，未调用大模型）。")

mode = st.radio("处理模式", ["手动结构化事件模式", "真实模型模式"], horizontal=True)
if mode == "手动结构化事件模式":
    st.info("未调用大模型，仅验证业务流程：请在下方填写结构化事件 JSON。")
else:
    st.info("真实模型模式：DeepSeek（deepseek-flash，DEEPSEEK_API_KEY）。未配置或失败会明确报错，不自动切换模式。")

tab_msg, tab_ledger, tab_review, tab_dashboard, tab_sync = st.tabs(["消息处理", "招聘台账", "待确认", "招聘看板", "同步管理"])


# ====================================================================== 消息处理
with tab_msg:
    st.subheader("消息处理")

    if "msg_seq" not in st.session_state:
        st.session_state.msg_seq = 1

    with st.form("process_form"):
        c1, c2, c3 = st.columns(3)
        source = c1.text_input("来源 source", value="mock_wecom", key="src")
        group_id = c2.text_input("群组 group_id", value="G_RECRUIT_001", key="grp")
        message_id = c3.text_input("消息ID message_id", value=f"M{st.session_state.msg_seq:03d}", key="mid")
        c4, c5 = st.columns(2)
        sender_id = c4.text_input("发送人ID", value="HR001", key="sid")
        sender_name = c4.text_input("发送人姓名", value="林悦", key="sname")
        sent_at = c5.text_input("发送时间 sent_at（Asia/Shanghai）", value="2026-11-19T09:00:00+08:00", key="sent")
        text = st.text_area("消息原文", height=90, key="text")

        if mode == "手动结构化事件模式":
            event_json = st.text_area(
                "结构化事件 JSON（经 Pydantic 校验后交给同一 Harness）",
                height=180,
                value=json.dumps(_EXAMPLE_EVENT, ensure_ascii=False, indent=2),
                key="event_json",
            )
        else:
            event_json = None

        submitted = st.form_submit_button("处理", key="submit")

    if submitted:
        delivery = {
            "delivery_id": f"D-{message_id}",
            "source": source,
            "group_id": group_id,
            "message_id": message_id,
            "sent_at": sent_at,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "text": text,
        }
        outcome = _process_message(repo, delivery, event_json, mode)
        _render_outcome(outcome)
        if outcome.status not in ("duplicate", "failed"):
            st.session_state.msg_seq += 1


# ====================================================================== 招聘台账
with tab_ledger:
    st.subheader("招聘台账")

    candidates = {c["candidate_id"]: c["name"] for c in repo.list_candidates()}
    applications = repo.list_applications()
    interviews = repo.list_interviews()

    f_name = st.text_input("按姓名筛选", key="f_name")
    f_position = st.text_input("按岗位筛选", key="f_position")
    stages = ["全部"] + sorted({a["stage"] for a in applications})
    f_stage = st.selectbox("按阶段筛选", stages, key="f_stage")

    rows = []
    for a in applications:
        name = candidates.get(a["candidate_id"], a["candidate_id"])
        if f_name and f_name not in name:
            continue
        if f_position and f_position not in a["position"]:
            continue
        if f_stage != "全部" and a["stage"] != f_stage:
            continue
        ivs = [i for i in interviews if i["application_id"] == a["application_id"]]
        iv_str = "；".join(
            f"第{i['round']}轮 {i['scheduled_at']} {i['interviewer']}（{i['status']}）"
            for i in ivs
        )
        rows.append({
            "候选人编号": a["candidate_id"],
            "姓名": name,
            "岗位": a["position"],
            "阶段": a["stage"],
            "渠道": a["channel"],
            "负责人": a["hr_owner"],
            "面试": iv_str or "—",
        })

    if rows:
        st.dataframe(rows, use_container_width=True)
    else:
        st.info("暂无台账记录（可用“消息处理”页新增，或用“待确认”页处理歧义）。")

    app_map = {}
    for a in applications:
        label = f"{a['application_id']} {candidates.get(a['candidate_id'], '')} · {a['position']}（{a['stage']}）"
        app_map[label] = a["application_id"]
    labels = list(app_map.keys())
    if labels:
        selected_label = st.selectbox("选择应聘记录查看详情", labels, key="app_detail")
        if selected_label:
            selected_app = app_map[selected_label]
            app = repo.get_application(selected_app)
            st.markdown("**变更历史**")
            history_rows = _change_history_rows(repo, selected_app)
            if history_rows:
                st.dataframe(history_rows, use_container_width=True)
            else:
                st.write("（暂无变更历史）")


# ====================================================================== 待确认
with tab_review:
    st.subheader("待确认事项")

    open_reviews = repo.list_open_reviews()
    if not open_reviews:
        st.info("暂无待确认事项。")
    else:
        candidates = {c["candidate_id"]: c["name"] for c in repo.list_candidates()}
        for review in open_reviews:
            with st.container(border=True):
                original = repo.find_raw_message_by_message_id(review["message_id"])
                st.write(f"**原消息 {review['message_id']}**：{original['text'] if original else '（未找到原文）'}")
                st.write(f"歧义原因：{review['reason']}")

                target_map = {}
                for aid in review["possible_application_ids"]:
                    a = repo.get_application(aid)
                    if a:
                        label = f"{a['candidate_id']} {candidates.get(a['candidate_id'], '')} · {a['position']}（{a['stage']}）"
                        target_map[label] = aid
                if not target_map:
                    st.write("（无可选目标应聘记录）")
                    continue

                target_label = st.selectbox(
                    "选择目标应聘记录",
                    list(target_map.keys()),
                    key=f"target_{review['message_id']}",
                )
                operator = st.text_input("操作者", value="操作员", key=f"operator_{review['message_id']}")

                if st.button("确认", key=f"confirm_{review['message_id']}"):
                    target = target_map[target_label]
                    app = repo.get_application(target)
                    confirm_event = StructuredEvent(
                        event_type=EventType.confirm,
                        references_message_id=review["message_id"],
                        candidate_id=app["candidate_id"],
                        position=app["position"],
                    )
                    confirm_delivery = {
                        "delivery_id": f"D-CONFIRM-{review['message_id']}",
                        "source": original["source"] if original else "ui",
                        "group_id": review.get("group_id") or group_id,
                        "message_id": f"M-CONFIRM-{review['message_id']}",
                        "sent_at": _now_shanghai(),
                        "sender_id": operator,
                        "sender_name": operator,
                        "text": f"确认 {review['message_id']} 指向 {app['candidate_id']} {app['position']}",
                    }
                    harness = DeliveryHarness(repo)
                    outcome = harness.process_with_event(confirm_delivery, confirm_event)
                    st.write(f"确认结果：{outcome.status}")
                    st.rerun()


# ====================================================================== 招聘看板
with tab_dashboard:
    st.subheader("招聘看板")

    use_fixed = st.checkbox("使用固定统计基准时间", value=False, key="use_fixed_base")
    if use_fixed:
        now = datetime.now(TZ_SHANGHAI)
        base_date = st.date_input("基准日期", value=now.date(), key="base_date")
        base_time = st.time_input("基准时间", value=now.time().replace(tzinfo=None), key="base_time")
        base_dt = datetime.combine(base_date, base_time, tzinfo=TZ_SHANGHAI)
    else:
        base_dt = datetime.now(TZ_SHANGHAI)
    stale_days = st.number_input("跟进超期阈值（天）", min_value=1, value=7, key="stale_days")

    st.caption(f"统计基准时间：{base_dt.strftime('%Y-%m-%d %H:%M:%S')}（Asia/Shanghai）")

    stats = compute_stats(repo, base_dt, int(stale_days))

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("候选人数", stats.candidate_count)
    c2.metric("应聘记录数", stats.application_count)
    c3.metric("未解决待确认", stats.open_review_count)
    c4.metric("待同步任务", stats.sync_pending_count)
    c5.metric("同步失败任务", stats.sync_failed_count)

    st.markdown("**当前阶段分布**（非转化率）")
    if stats.stage_distribution:
        st.bar_chart(stats.stage_distribution)
    else:
        st.info("暂无阶段分布数据")

    st.markdown("**今日及未来待进行面试**")
    if stats.upcoming_interviews:
        st.dataframe(
            [
                {
                    "应聘记录": iv["application_id"],
                    "轮次": iv["round"],
                    "时间": iv["scheduled_at"],
                    "面试官": iv["interviewer"],
                    "状态": iv["status"],
                }
                for iv in stats.upcoming_interviews
            ],
            use_container_width=True,
        )
    else:
        st.info("暂无待进行面试")

    st.markdown(f"**长时间未更新的跟进记录**（阈值 {stale_days} 天）")
    if stats.stale_applications:
        st.dataframe(
            [
                {
                    "候选人编号": a["candidate_id"],
                    "岗位": a["position"],
                    "阶段": a["stage"],
                    "负责人": a["hr_owner"],
                    "最后变更": a["last_change"],
                }
                for a in stats.stale_applications
            ],
            use_container_width=True,
        )
    else:
        st.info("无超期跟进记录")


# ====================================================================== 同步管理
with tab_sync:
    st.subheader("同步管理")
    st.caption("腾讯文档未接入：以下为“模拟外部表格”；同步成功仅表示“模拟同步成功”。")

    mock_sync_db = os.environ.get(
        "RECRUITFLOW_MOCK_SYNC_DB",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "mock-sync.db"),
    )
    mock_fail = st.checkbox("模拟下一次同步临时失败", value=False, key="mock_fail")

    if st.button("执行到期任务", key="run_due"):
        adapter = MockSyncAdapter(mock_sync_db, fail_first_n=1 if mock_fail else 0)
        results = run_due_tasks(repo, adapter)
        st.write("执行结果：", [r["status"] for r in results] or "（无到期任务）")

    if st.button("重试全部失败任务", key="retry_failed"):
        for t in repo.list_sync_tasks():
            if t["status"] == "failed":
                retry_failed(repo, t["task_id"])
        st.write("已重置失败任务为待同步，请点“执行到期任务”。")

    status_labels = {
        "pending": "待同步",
        "processing": "处理中",
        "retry_wait": "等待重试",
        "succeeded": "模拟同步成功",
        "failed": "失败",
    }
    tasks = repo.list_sync_tasks()
    st.markdown("**同步任务**")
    if tasks:
        st.dataframe(
            [
                {
                    "任务": t["task_id"],
                    "应聘记录": t.get("application_id"),
                    "状态": status_labels.get(t["status"], t["status"]),
                    "尝试次数": t["attempts"],
                    "下次重试": t.get("next_retry_at"),
                    "错误原因": t.get("last_error"),
                }
                for t in tasks
            ],
            use_container_width=True,
        )
    else:
        st.info("暂无同步任务")

    st.markdown("**模拟外部表格内容**")
    records = MockSyncAdapter(mock_sync_db).list_records()
    if records:
        st.dataframe(records, use_container_width=True)
    else:
        st.info("（模拟外部表格为空）")
