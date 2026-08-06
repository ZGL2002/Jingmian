"""LLM 客户端：统一接口 + DeepSeek（OpenAI 兼容协议）。"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from openai import OpenAI, AuthenticationError


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AssistantTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient:
    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None) -> AssistantTurn:
        raise NotImplementedError


class DeepSeekClient(LLMClient):
    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self._client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self._model = model

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None) -> AssistantTurn:
        try:
            kwargs: dict = {"model": self._model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            msg = self._client.chat.completions.create(**kwargs).choices[0].message
        except AuthenticationError:
            raise LLMError("DeepSeek API key 无效或未授权") from None
        except Exception as e:  # noqa: BLE001 - 统一包装为 LLMError 由上层重试/提示
            raise LLMError(f"DeepSeek 调用失败: {e}") from None
        tool_calls = [
            ToolCall(id=t.id, name=t.function.name, arguments=json.loads(t.function.arguments or "{}"))
            for t in (msg.tool_calls or [])
        ]
        return AssistantTurn(content=msg.content, tool_calls=tool_calls)
