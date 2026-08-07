import time
from interview_agent.web.runner import InterviewTask
from interview_agent.web.events import EventQueue
from interview_agent.session import InterviewSession
from interview_agent.models import SessionConfig, SessionState
from interview_agent.tools import default_registry
from interview_agent.tools.base import ToolContext
from interview_agent.agent import ToolAgent
from interview_agent.security import PathPolicy
from interview_agent.llm import AssistantTurn, StreamEnd


class TaskLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def make_task(tmp_path, script):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1)
    s = InterviewSession(cfg)
    s.start()
    ctx = ToolContext(
        user_id="alice", session_dir=s.session_dir,
        policy=PathPolicy([tmp_path]), transcript_path=s.transcript_path,
        wrap_allowed=s.can_auto_wrap,
    )
    llm = TaskLLM(script)
    agent = ToolAgent(llm, default_registry(), s, ctx)
    return InterviewTask("alice", s, agent, EventQueue()), s


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_full_flow_opens_answers_evaluates(tmp_path):
    task, s = make_task(tmp_path, [
        AssistantTurn(content="开场"),
        AssistantTurn(content="问题一"),
        AssistantTurn(content="## 评估"),
    ])
    task.start()
    assert wait_until(lambda: any(e["type"] == "turn_end" for e in task.queue.snapshot()))
    task.submit_answer("回答1")
    assert wait_until(lambda: any(e["type"] == "turn_end" and e["question_count"] == 1 for e in task.queue.snapshot()))
    task.request_end()
    assert wait_until(lambda: any(e.get("status") == "done" for e in task.queue.snapshot()))
    assert s.state == SessionState.DONE
    assert task.report_url == f"/api/sessions/{s.session_id}/report"


def test_busy_rejects_submit(tmp_path):
    task, _ = make_task(tmp_path, [AssistantTurn(content="开场")])
    task.busy = True
    try:
        task.submit_answer("x")
        assert False, "应当抛出 RuntimeError"
    except RuntimeError:
        pass


def test_shutdown_stops_without_evaluation(tmp_path):
    task, s = make_task(tmp_path, [AssistantTurn(content="开场")])
    task.start()
    assert wait_until(lambda: any(e["type"] == "turn_end" for e in task.queue.snapshot()))
    task.shutdown.set()
    assert wait_until(lambda: task.ended)
    assert not any(e.get("status") == "done" for e in task.queue.snapshot())
    assert s.state != SessionState.DONE
