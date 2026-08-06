"""对话记录与收尾信号工具。"""
from __future__ import annotations
from .base import Tool
from ..storage import append_jsonl, timestamp


def _append_transcript(args: dict, ctx) -> str:
    append_jsonl(
        ctx.transcript_path,
        {
            "role": args.get("role", "note"),
            "content": args["content"],
            "timestamp": timestamp(),
        },
    )
    return "已记录"


def _request_wrap(args: dict, ctx) -> str:
    if ctx.wrap_allowed():
        return "WRAP_REQUESTED"
    return "错误：尚未达到最低题数，不允许收尾，请继续提问"


def build_transcript_tools() -> list[Tool]:
    return [
        Tool(
            "append_transcript", "把一条记录追加到对话记录（role 默认 note，用于记录观察或备注）。",
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "role": {"type": "string"},
                },
                "required": ["content"],
            },
            _append_transcript,
        ),
        Tool(
            "request_wrap", "请求结束面试并进入评估。仅在达到最低题数后使用。",
            {"type": "object", "properties": {}},
            _request_wrap,
        ),
    ]
