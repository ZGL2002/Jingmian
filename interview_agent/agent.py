"""工具型 Agent 循环：思考 → 调用工具 → 观察 → 继续。"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass
from .tools import ToolRegistry
from .tools.base import ToolContext
from .session import InterviewSession
from .llm import LLMClient, LLMError

INTERVIEW_TURN_MAX_TOKENS = 1000


def _strip_role_leak(content: str) -> str:
    """防御性截断：模型输出若出现"你>"提示符（自问自答串台），只保留提示符之前的内容。"""
    idx = content.find("你>")
    if idx != -1:
        return content[:idx].rstrip()
    return content


@dataclass
class AgentTurnResult:
    content: str
    wrap_requested: bool = False


class ToolAgent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        session: InterviewSession,
        tool_ctx: ToolContext,
        max_iterations: int = 8,
    ):
        self.llm = llm
        self.registry = registry
        self.session = session
        self.tool_ctx = tool_ctx
        self.max_iterations = max_iterations

    def _chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None):
        last_error: LLMError | None = None
        for attempt in range(3):
            try:
                return self.llm.chat(messages, tools=tools, max_tokens=max_tokens)
            except LLMError as e:
                last_error = e
                time.sleep(0.5 * (2 ** attempt))
        assert last_error is not None
        raise last_error

    def run_turn(self) -> AgentTurnResult:
        wrap_requested = False
        failed = 0
        for _ in range(self.max_iterations):
            turn = self._chat(
                self.session.messages,
                tools=self.registry.schemas(),
                max_tokens=INTERVIEW_TURN_MAX_TOKENS,
            )
            if not turn.tool_calls:
                content = _strip_role_leak(turn.content or "")
                self.session.add_interviewer_message(content)
                return AgentTurnResult(content=content, wrap_requested=wrap_requested)
            tool_msgs = []
            for tc in turn.tool_calls:
                result = self.registry.execute(tc.name, tc.arguments, self.tool_ctx)
                if tc.name == "request_wrap" and result == "WRAP_REQUESTED":
                    self.session.to_wrapping()
                    wrap_requested = True
                failed = failed + 1 if result.startswith("错误：") else 0
                tool_msgs.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            self.session.messages.append({
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in turn.tool_calls
                ],
            })
            self.session.messages.extend(tool_msgs)
            if failed >= 3:
                self.session.messages.append({
                    "role": "system",
                    "content": "工具连续失败 3 次：请停止调用工具，直接用文字继续面试。",
                })
                break
        turn = self._chat(self.session.messages, max_tokens=INTERVIEW_TURN_MAX_TOKENS)
        content = _strip_role_leak(turn.content or "")
        self.session.add_interviewer_message(content)
        return AgentTurnResult(content=content, wrap_requested=wrap_requested)
