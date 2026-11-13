"""提取提示词与用户消息构造（供各模型适配器复用）。"""
from __future__ import annotations

import json
from pathlib import Path

from .base import MessageContext

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "extract.md"


def load_extract_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_user_message(message: MessageContext) -> str:
    """把消息与元数据、业务上下文拼成给模型的用户输入。"""
    lines = [
        "请从下面的企业微信招聘消息中提取结构化招聘事件。",
        f"消息原文：{message.text}",
        f"发送时间 sent_at（Asia/Shanghai）：{message.sent_at}",
    ]
    if message.sender_id or message.sender_name:
        sender = message.sender_name or ""
        sid = f"（{message.sender_id}）" if message.sender_id else ""
        lines.append(f"发送人：{sender}{sid}")
    if message.message_id:
        lines.append(f"message_id：{message.message_id}")
    if message.source:
        lines.append(f"source：{message.source}")
    if message.group_id:
        lines.append(f"group_id：{message.group_id}")
    if message.business_context:
        ctx = json.dumps(message.business_context, ensure_ascii=False, default=str)
        lines.append(f"业务上下文（仅供匹配线索）：{ctx}")
    lines.append("请按系统提示中的规则与 JSON Schema 输出一个事件对象。")
    return "\n".join(lines)
