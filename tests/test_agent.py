from interview_agent.agent import ToolAgent, AgentTurnResult
from interview_agent.session import InterviewSession
from interview_agent.models import SessionConfig, SessionState
from interview_agent.tools import default_registry
from interview_agent.tools.base import ToolContext
from interview_agent.llm import AssistantTurn, ToolCall
from interview_agent.security import PathPolicy
from interview_agent.storage import read_jsonl

class ScriptedLLM:
    def __init__(self, turns):
        self.turns = list(turns)
        self.last_max_tokens = None
    def chat(self, messages, tools=None, max_tokens=None):
        self.last_max_tokens = max_tokens
        return self.turns.pop(0)

def make_agent(tmp_path, turns, min_questions=2):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=min_questions)
    s = InterviewSession(cfg)
    s.start()
    s.begin_questions()
    ctx = ToolContext(
        user_id="alice", session_dir=s.session_dir,
        policy=PathPolicy([tmp_path]), transcript_path=s.transcript_path,
        wrap_allowed=s.can_auto_wrap,
    )
    agent = ToolAgent(llm=ScriptedLLM(turns), registry=default_registry(), session=s, tool_ctx=ctx)
    return agent, s

def test_plain_answer_no_tools(tmp_path):
    agent, s = make_agent(tmp_path, [AssistantTurn(content="请介绍一下项目")])
    r = agent.run_turn()
    assert r == AgentTurnResult(content="请介绍一下项目", wrap_requested=False)
    assert s.question_count == 1

def test_tool_call_then_answer(tmp_path):
    turns = [
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t1", name="append_transcript", arguments={"content": "候选人犹豫了"})]),
        AssistantTurn(content="能具体说说吗？"),
    ]
    agent, s = make_agent(tmp_path, turns)
    r = agent.run_turn()
    assert r.content == "能具体说说吗？"
    assert any(e["role"] == "note" for e in read_jsonl(s.transcript_path))

def test_wrap_requested_after_min(tmp_path):
    turns = [
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t1", name="request_wrap", arguments={})]),
        AssistantTurn(content="好的，我们进入收尾。"),
    ]
    agent, s = make_agent(tmp_path, turns, min_questions=1)
    s.add_interviewer_message("第一个问题？")  # question_count -> 1
    r = agent.run_turn()
    assert r.wrap_requested
    assert s.state == SessionState.WRAPPING
    assert s.question_count == 1  # 收尾语不再计数

def test_wrap_rejected_before_min(tmp_path):
    turns = [
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t1", name="request_wrap", arguments={})]),
        AssistantTurn(content="继续下一个问题。"),
    ]
    agent, s = make_agent(tmp_path, turns, min_questions=3)
    r = agent.run_turn()
    assert not r.wrap_requested
    assert s.state == SessionState.QUESTIONING
    assert r.content == "继续下一个问题。"

def test_three_failures_force_content(tmp_path):
    failing = AssistantTurn(content=None, tool_calls=[ToolCall(id=f"t{i}", name="read_file", arguments={"path": "missing.txt"}) for i in range(1)])
    turns = [failing, failing, failing, AssistantTurn(content="请继续。")]
    agent, s = make_agent(tmp_path, turns, min_questions=5)
    r = agent.run_turn()
    assert r.content == "请继续。"
    assert s.state == SessionState.QUESTIONING

class FlakyLLM:
    def __init__(self, failures, content):
        self.failures = failures
        self.content = content
        self.calls = 0
    def chat(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        if self.calls <= self.failures:
            from interview_agent.llm import LLMError
            raise LLMError("临时错误")
        return AssistantTurn(content=self.content)

def test_llm_retry_after_transient_error(tmp_path):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1)
    s = InterviewSession(cfg)
    s.start()
    s.begin_questions()
    ctx = ToolContext(
        user_id="alice", session_dir=s.session_dir,
        policy=PathPolicy([tmp_path]), transcript_path=s.transcript_path,
        wrap_allowed=s.can_auto_wrap,
    )
    flaky = FlakyLLM(failures=2, content="重试成功")
    agent = ToolAgent(llm=flaky, registry=default_registry(), session=s, tool_ctx=ctx)
    r = agent.run_turn()
    assert r.content == "重试成功"
    assert flaky.calls == 3


def test_chat_with_retry_extra_attempts(tmp_path):
    # 开场路径：免费档限流可连续多次，attempts 参数允许更多重试
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1)
    s = InterviewSession(cfg)
    s.start()
    ctx = ToolContext(
        user_id="alice", session_dir=s.session_dir,
        policy=PathPolicy([tmp_path]), transcript_path=s.transcript_path,
        wrap_allowed=s.can_auto_wrap,
    )
    flaky = FlakyLLM(failures=4, content="第五次成功")
    agent = ToolAgent(llm=flaky, registry=default_registry(), session=s, tool_ctx=ctx)
    turn = agent.chat_with_retry(s.messages, attempts=5)
    assert turn.content == "第五次成功"
    assert flaky.calls == 5

def test_content_truncated_at_role_leak(tmp_path):
    agent, s = make_agent(tmp_path, [AssistantTurn(content="请介绍你的项目。\n你> 我觉得这个项目很有挑战")])
    r = agent.run_turn()
    assert r.content == "请介绍你的项目。"
    entries = read_jsonl(s.transcript_path)
    assert "你>" not in entries[-1]["content"]

def test_turn_chat_passes_max_tokens_cap(tmp_path):
    agent, s = make_agent(tmp_path, [AssistantTurn(content="问题？")])
    agent.run_turn()
    assert agent.llm.last_max_tokens == 1000
