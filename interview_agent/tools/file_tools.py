"""文件读写工具。"""
from __future__ import annotations
from .base import Tool
from ..storage import atomic_write
from ..security import check_owner_if_marked


def _read(args: dict, ctx) -> str:
    p = ctx.policy.resolve(args["path"])
    if not p.is_file():
        return f"错误：文件不存在 {args['path']}"
    check_owner_if_marked(p, ctx.user_id)
    text = p.read_text(encoding="utf-8", errors="replace")
    if "max_chars" in args:
        text = text[: int(args["max_chars"])]
    return text


def _write(args: dict, ctx) -> str:
    p = ctx.policy.resolve(args["path"])
    check_owner_if_marked(p, ctx.user_id)
    atomic_write(p, args["content"])
    return f"已写入 {p.name}"


def _append(args: dict, ctx) -> str:
    p = ctx.policy.resolve(args["path"])
    check_owner_if_marked(p, ctx.user_id)
    with p.open("a", encoding="utf-8") as f:
        f.write(args["content"])
    return f"已追加 {p.name}"


def _edit(args: dict, ctx) -> str:
    p = ctx.policy.resolve(args["path"])
    check_owner_if_marked(p, ctx.user_id)
    text = p.read_text(encoding="utf-8")
    count = text.count(args["old_text"])
    if count != 1:
        return f"错误：old_text 出现 {count} 次（需要恰好 1 次）"
    atomic_write(p, text.replace(args["old_text"], args["new_text"]))
    return f"已修改 {p.name}"


def _list_dir(args: dict, ctx) -> str:
    raw = args.get("path", "")
    p = ctx.session_dir if not raw else ctx.policy.resolve(raw)
    if not p.is_dir():
        return f"错误：不是目录 {raw}"
    return "\n".join(sorted(x.name for x in p.iterdir()))


def build_file_tools() -> list[Tool]:
    return [
        Tool(
            "read_file", "读取文本文件内容；path 相对会话根目录。",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
            _read,
        ),
        Tool(
            "write_file", "写入/覆盖一个文本文件；path 相对会话根目录。",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            _write,
        ),
        Tool(
            "append_file", "向文本文件末尾追加内容。",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            _append,
        ),
        Tool(
            "edit_file", "精确替换文件中唯一出现的一段文本。",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            _edit,
        ),
        Tool(
            "list_dir", "列出目录中的文件名；path 为空时列会话根目录。",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            _list_dir,
        ),
    ]
