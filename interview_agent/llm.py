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


@dataclass
class StreamEnd:
    """流式输出的收尾标记，携带完整 AssistantTurn。"""
    turn: AssistantTurn


class LLMClient:
    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None) -> AssistantTurn:
        raise NotImplementedError

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None):
        """逐段产出 str 文本增量；最后一个元素是 StreamEnd。"""
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

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None):
        kwargs: dict = {"model": self._model, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        try:
            stream = self._client.chat.completions.create(**kwargs)
            content_parts: list[str] = []
            tool_acc: dict[int, dict] = {}
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:
                    content_parts.append(delta.content)
                    yield delta.content
                for tc in (delta.tool_calls or []):
                    acc = tool_acc.setdefault(tc.index, {"id": tc.id or "", "name": "", "arguments": ""})
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function and tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        acc["arguments"] += tc.function.arguments
            tool_calls = []
            for idx in sorted(tool_acc):
                acc = tool_acc[idx]
                tool_calls.append(ToolCall(
                    id=acc["id"],
                    name=acc["name"],
                    arguments=json.loads(acc["arguments"] or "{}"),
                ))
            yield StreamEnd(AssistantTurn(
                content="".join(content_parts) or None,
                tool_calls=tool_calls,
            ))
        except AuthenticationError:
            raise LLMError("DeepSeek API key 无效或未授权") from None
        except Exception as e:  # noqa: BLE001 - 统一包装为 LLMError 由上层重试/提示
            raise LLMError(f"DeepSeek 调用失败: {e}") from None


def create_llm(api_key: str, model: str = "deepseek-chat", provider: str = "deepseek") -> LLMClient:
    """LLM 客户端工厂：未来多用户 BYOK 只改这里传入的 api_key 来源。"""
    if provider == "deepseek":
        return DeepSeekClient(api_key=api_key, model=model)
    raise ValueError(f"不支持的 provider: {provider}")
