"""独立调用脚本：输入一条消息，显示校验后的结构化事件。

不写数据库、不连接企业微信或腾讯文档。默认使用真实模型适配器（Anthropic SDK），
密钥从环境变量 ANTHROPIC_API_KEY 读取；未配置时明确报错，不静默降级为 Fake。

用法示例：
    python -m scripts.extract_one "C001后端开发一面定在明天下午3点，面试官陈峰。" \
        --sent-at "2026-11-16T10:10:00+08:00" --sender-name 林悦
"""
from __future__ import annotations

import argparse
import json
import sys

from adapters import AnthropicAdapter, DeepSeekAdapter, MessageContext, ZhipuAdapter
from adapters.errors import AdapterError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="提取一条招聘消息的结构化事件")
    p.add_argument("text", help="消息原文")
    p.add_argument("--sent-at", required=True, help="消息发送时间（ISO 8601，Asia/Shanghai）")
    p.add_argument("--message-id", default=None)
    p.add_argument("--source", default=None)
    p.add_argument("--group-id", default=None)
    p.add_argument("--sender-id", default=None)
    p.add_argument("--sender-name", default=None)
    p.add_argument("--context-json", default=None, help="业务上下文 JSON 字符串")
    p.add_argument("--model", default=None, help="覆盖模型名称环境变量")
    p.add_argument("--base-url", default=None, help="覆盖 API 地址环境变量")
    p.add_argument("--timeout", type=float, default=None, help="覆盖超时环境变量")
    p.add_argument("--max-retries", type=int, default=None, help="限流重试次数（默认 3；0 表示关闭重试）")
    p.add_argument(
        "--provider",
        choices=["deepseek", "zhipu", "anthropic"],
        default="deepseek",
        help="模型提供方（默认 deepseek，读取 DEEPSEEK_API_KEY）",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    business_context = None
    if args.context_json:
        try:
            business_context = json.loads(args.context_json)
        except json.JSONDecodeError as exc:
            print(f"错误：--context-json 不是合法 JSON：{exc}", file=sys.stderr)
            return 2

    message = MessageContext(
        text=args.text,
        sent_at=args.sent_at,
        message_id=args.message_id,
        source=args.source,
        group_id=args.group_id,
        sender_id=args.sender_id,
        sender_name=args.sender_name,
        business_context=business_context,
    )

    try:
        kwargs = {"base_url": args.base_url, "model": args.model, "timeout": args.timeout}
        if args.max_retries is not None and args.provider in ("deepseek", "zhipu"):
            kwargs["max_retries"] = args.max_retries
        if args.provider == "deepseek":
            adapter = DeepSeekAdapter(**kwargs)
        elif args.provider == "zhipu":
            adapter = ZhipuAdapter(**kwargs)
        else:
            adapter = AnthropicAdapter(
                base_url=args.base_url, model=args.model, timeout=args.timeout
            )
        result = adapter.extract(message)
    except AdapterError as exc:
        parts = [f"提取失败：{exc}"]
        req_id = getattr(exc, "request_id", None)
        retry_after = getattr(exc, "retry_after", None)
        if req_id:
            parts.append(f"request_id={req_id}")
        if retry_after is not None:
            parts.append(f"Retry-After={retry_after}")
        print(" | ".join(parts), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "event": result.event.model_dump(mode="json"),
                "model_name": result.model_name,
                "elapsed_seconds": round(result.elapsed_seconds, 4),
                "usage": result.usage,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
