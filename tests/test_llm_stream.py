from interview_agent.llm import DeepSeekClient, StreamEnd, AssistantTurn, ToolCall


class Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, delta):
        self.delta = delta


class FakeChunk:
    def __init__(self, delta):
        self.choices = [FakeChoice(delta)]


class FakeToolCallDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.chunks)


class FakeClient:
    def __init__(self, chunks):
        self.chat = type("Chat", (), {"completions": FakeCompletions(chunks)})()


def _client(chunks):
    c = DeepSeekClient("sk-test")
    c._client = FakeClient(chunks)
    return c


def test_chat_stream_text_deltas_and_end():
    c = _client([FakeChunk(Delta(content="你")), FakeChunk(Delta(content="好"))])
    chunks = list(c.chat_stream([{"role": "user", "content": "hi"}], max_tokens=10))
    assert "".join(x for x in chunks if isinstance(x, str)) == "你好"
    end = chunks[-1]
    assert isinstance(end, StreamEnd)
    assert end.turn == AssistantTurn(content="你好", tool_calls=[])


def test_chat_stream_tool_calls_accumulated():
    c = _client([
        FakeChunk(Delta(tool_calls=[FakeToolCallDelta(0, id="t1", name="read_file")])),
        FakeChunk(Delta(tool_calls=[FakeToolCallDelta(0, arguments='{"path": "a.txt"}')])),
    ])
    chunks = list(c.chat_stream([], tools=[{"type": "function"}]))
    end = chunks[-1]
    assert end.turn.content is None
    assert end.turn.tool_calls == [ToolCall(id="t1", name="read_file", arguments={"path": "a.txt"})]


def test_chat_stream_passes_stream_flag():
    c = _client([FakeChunk(Delta(content="a"))])
    list(c.chat_stream([], tools=None, max_tokens=5))
    assert c._client.chat.completions.kwargs["stream"] is True
