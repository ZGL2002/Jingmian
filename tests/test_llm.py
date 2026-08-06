import pytest
from interview_agent.llm import LLMClient, DeepSeekClient, LLMError, AssistantTurn, ToolCall

class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

class FakeToolCall:
    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = type("F", (), {"name": name, "arguments": arguments})()

class FakeCompletions:
    def __init__(self, message, error=None):
        self.message = message
        self.error = error
        self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return type("R", (), {"choices": [type("C", (), {"message": self.message})()]})()

class FakeClient:
    def __init__(self, completions):
        self.chat = type("Ch", (), {"completions": completions})()

def make_client(monkeypatch, message, error=None):
    import interview_agent.llm as mod
    completions = FakeCompletions(message, error)
    monkeypatch.setattr(mod, "OpenAI", lambda *a, **k: FakeClient(completions))
    return DeepSeekClient("sk-test"), completions

def test_base_chat_not_implemented():
    with pytest.raises(NotImplementedError):
        LLMClient().chat([])

def test_deepseek_content_passthrough(monkeypatch):
    client, completions = make_client(monkeypatch, FakeMessage(content="你好"))
    out = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert out == AssistantTurn(content="你好", tool_calls=[])
    assert completions.calls[0]["model"] == "deepseek-chat"
    assert completions.calls[0]["tools"] == [{"type": "function"}]

def test_deepseek_tool_call_parsed(monkeypatch):
    client, _ = make_client(monkeypatch, FakeMessage(content=None, tool_calls=[FakeToolCall("t1", "read_file", '{"path":"a.txt"}')]))
    out = client.chat([])
    assert out.content is None
    assert out.tool_calls == [ToolCall(id="t1", name="read_file", arguments={"path": "a.txt"})]

def test_auth_error_mapped(monkeypatch):
    import interview_agent.llm as mod
    class AuthError(Exception):
        pass
    monkeypatch.setattr(mod, "AuthenticationError", AuthError)
    client, _ = make_client(monkeypatch, FakeMessage(), error=AuthError())
    with pytest.raises(LLMError):
        client.chat([])
