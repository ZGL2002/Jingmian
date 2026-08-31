"""工具基类与注册表。"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from ..security import PathPolicy


@dataclass
class ToolContext:
    user_id: str
    session_dir: Path
    policy: PathPolicy
    transcript_path: Path
    wrap_allowed: Callable[[], bool] = lambda: True
    github_token: str = ""


class Tool:
    def __init__(self, name: str, description: str, input_schema: dict, handler):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def run(self, args: dict, ctx: ToolContext) -> str:
        return self.handler(args, ctx)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, args: dict, ctx: ToolContext) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"错误：未知工具 {name}"
        try:
            return tool.run(args, ctx)
        except Exception as e:  # noqa: BLE001 - 工具错误以字符串返回给模型
            return f"错误：工具执行失败 {e}"
