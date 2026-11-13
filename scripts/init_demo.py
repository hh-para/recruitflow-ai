"""离线演示初始化：通过 Harness 重放人工标注事件，不调用真实模型、不导入最终状态。

用法（项目根目录）：
    python -m scripts.init_demo           # 演示库已有数据则拒绝覆盖
    python -m scripts.init_demo --reset   # 显式重置专用演示库
    python -m scripts.init_demo --db <路径>  # 指定演示库路径
"""
from __future__ import annotations

import argparse
import os
import sys

from harness.demo_events import HUMAN_DELIVERIES, build_delivery
from harness.processor import DeliveryHarness
from storage import LedgerRepository, init_db

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "recruitflow-demo.db"
)
_MARKER_SUFFIX = ".demo"


def _clear(conn) -> None:
    for table in (
        "sync_tasks",
        "reviews",
        "business_events",
        "interviews",
        "applications",
        "candidates",
        "raw_messages",
    ):
        conn.execute(f"DELETE FROM {table}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="离线演示初始化（人工标注事件演示）")
    p.add_argument("--reset", action="store_true", help="显式重置专用演示库（清空已有数据）")
    p.add_argument("--db", default=None, help="演示库路径（默认 recruitflow-demo.db）")
    args = p.parse_args(argv)

    db_path = args.db or os.environ.get("RECRUITFLOW_DB_PATH") or _DEFAULT_DB
    db_path = os.path.abspath(db_path)
    conn = init_db(db_path)
    repo = LedgerRepository(conn)

    has_data = bool(repo.list_candidates() or repo.list_raw_messages())
    if has_data and not args.reset:
        print(f"演示库 {db_path} 已有数据，拒绝覆盖；如需重置请加 --reset。", file=sys.stderr)
        return 1
    if args.reset:
        _clear(conn)

    harness = DeliveryHarness(repo)  # 无模型适配器：走 process_with_event
    for entry in HUMAN_DELIVERIES:
        harness.process_with_event(build_delivery(entry), entry["event"])

    # 写演示标记，供页面显示“人工标注事件演示”
    with open(db_path + _MARKER_SUFFIX, "w", encoding="utf-8") as f:
        f.write("human_annotated_demo\n")

    print(f"已初始化演示库：{db_path}")
    print(
        f"  候选人 {len(repo.list_candidates())}、应聘 {len(repo.list_applications())}、"
        f"面试 {len(repo.list_interviews())}、业务事件 {len(repo.list_business_events())}"
    )
    print("  页面将显示“人工标注事件演示”。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
