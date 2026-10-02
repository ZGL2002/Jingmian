"""LLM 客户端：统一接口 + DeepSeek（OpenAI 兼容协议）。"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from openai import OpenAI, AuthenticationError, RateLimitError


class LLMError(Exception):
    """LLM 调用失败；retry_after 非空表示限流/过载，等待该秒数后重试更有效。"""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


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


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容协议客户端基类：DeepSeek / 阿里云百炼（DashScope）等共用。"""

    BASE_URL: str = ""
    LABEL: str = "LLM"

    @staticmethod
    def _normalize_messages(messages: list[dict]) -> list[dict]:
        """智谱等 provider 要求对话中至少一条 user 消息：纯 system 开场会被 400（1214）拒绝。"""
        if messages and not any(m.get("role") == "user" for m in messages):
            return [*messages, {"role": "user", "content": "请开始。"}]
        return messages

    def __init__(self, api_key: str, model: str):
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.BASE_URL,
            timeout=60.0,
        )
        self._model = model

    def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None) -> AssistantTurn:
        try:
            kwargs: dict = {"model": self._model, "messages": self._normalize_messages(messages)}
            if tools:
                kwargs["tools"] = tools
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            msg = self._client.chat.completions.create(**kwargs).choices[0].message
        except AuthenticationError:
            raise LLMError(f"{self.LABEL} API key 无效或未授权") from None
        except RateLimitError:
            raise LLMError(f"{self.LABEL} 限流或模型过载，请稍后重试", retry_after=8.0) from None
        except Exception as e:  # noqa: BLE001 - 统一包装为 LLMError 由上层重试/提示
            raise LLMError(f"{self.LABEL} 调用失败: {e}") from None
        tool_calls = [
            ToolCall(id=t.id, name=t.function.name, arguments=json.loads(t.function.arguments or "{}"))
            for t in (msg.tool_calls or [])
        ]
        return AssistantTurn(content=msg.content, tool_calls=tool_calls)

    def chat_stream(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int | None = None):
        kwargs: dict = {"model": self._model, "messages": self._normalize_messages(messages), "stream": True}
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
            raise LLMError(f"{self.LABEL} API key 无效或未授权") from None
        except RateLimitError:
            raise LLMError(f"{self.LABEL} 限流或模型过载，请稍后重试", retry_after=8.0) from None
        except Exception as e:  # noqa: BLE001 - 统一包装为 LLMError 由上层重试/提示
            raise LLMError(f"{self.LABEL} 调用失败: {e}") from None


class DeepSeekClient(OpenAICompatibleClient):
    BASE_URL = "https://api.deepseek.com"
    LABEL = "DeepSeek"

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        super().__init__(api_key, model)


class DashScopeClient(OpenAICompatibleClient):
    """阿里云百炼（通义千问 / DeepSeek 托管等），OpenAI 兼容模式端点。"""

    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LABEL = "阿里云百炼"

    def __init__(self, api_key: str, model: str = "qwen-plus"):
        super().__init__(api_key, model)


class ZhipuClient(OpenAICompatibleClient):
    """智谱开放平台（GLM 系列），OpenAI 兼容模式端点。"""

    BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
    LABEL = "智谱"

    def __init__(self, api_key: str, model: str = "glm-4.7-flash"):
        super().__init__(api_key, model)


def create_llm(api_key: str, model: str | None = None, provider: str = "deepseek") -> LLMClient:
    """LLM 客户端工厂：未来多用户 BYOK 只改这里传入的 api_key 来源。model 省略时用各 provider 默认模型。"""
    if provider == "deepseek":
        return DeepSeekClient(api_key=api_key, model=model or "deepseek-chat")
    if provider == "dashscope":
        return DashScopeClient(api_key=api_key, model=model or "qwen-plus")
    if provider == "zhipu":
        return ZhipuClient(api_key=api_key, model=model or "glm-4.7-flash")
    raise ValueError(f"不支持的 provider: {provider}")
