from interview_agent.agent import ToolAgent
from interview_agent.session import InterviewSession
from interview_agent.models import SessionConfig
from interview_agent.tools import default_registry
from interview_agent.tools.base import ToolContext
from interview_agent.llm import AssistantTurn, ToolCall, StreamEnd
from interview_agent.security import PathPolicy


class StreamingLLM:
    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.turns.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.turns.pop(0)
        if turn.content:
            yield turn.content[:2]
            yield turn.content[2:]
        yield StreamEnd(turn)


def make_agent(tmp_path, turns, min_questions=1):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=min_questions)
    s = InterviewSession(cfg)
    s.start()
    s.begin_questions()
    ctx = ToolContext(
        user_id="alice", session_dir=s.session_dir,
        policy=PathPolicy([tmp_path]), transcript_path=s.transcript_path,
        wrap_allowed=s.can_auto_wrap,
    )
    return ToolAgent(llm=StreamingLLM(turns), registry=default_registry(), session=s, tool_ctx=ctx), s


def test_streaming_deltas_delivered(tmp_path):
    agent, s = make_agent(tmp_path, [AssistantTurn(content="请介绍项目")])
    deltas = []
    r = agent.run_turn(on_delta=deltas.append)
    assert "".join(deltas) == "请介绍项目"
    assert r.content == "请介绍项目"
    assert s.question_count == 1


def test_streaming_tool_turn_no_content_deltas(tmp_path):
    agent, s = make_agent(tmp_path, [
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t1", name="request_wrap", arguments={})]),
        AssistantTurn(content="好的，我们收尾。"),
    ])
    s.add_interviewer_message("问题一")  # question_count -> 1（>= min）
    deltas = []
    r = agent.run_turn(on_delta=deltas.append)
    assert "".join(deltas) == "好的，我们收尾。"
    assert r.wrap_requested
