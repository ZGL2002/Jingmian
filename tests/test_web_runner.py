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


def test_empty_timeout_does_not_crash(tmp_path):
    """开场后未回答、答案队列 get 超时（0.5s）不应触发 NameError。"""
    task, s = make_task(tmp_path, [AssistantTurn(content="开场")])
    task.start()
    assert wait_until(lambda: any(e["type"] == "turn_end" for e in task.queue.snapshot()))
    time.sleep(0.8)  # 越过 _answers.get(timeout=0.5) 的超时窗口
    assert not any(e["type"] == "error" for e in task.queue.snapshot())
    assert not task.ended
    assert task.error is None
    # 超时之后仍能正常提交回答
    task.submit_answer("回答1")
    assert task.busy


def test_answer_turn_drains_repo_injection_between_turns(tmp_path):
    """仓库分析在用户回答的同时完成：注入必须落在轮次边界（user 消息之前），不打断对话。"""
    from interview_agent.models import RepoAnalysis
    from interview_agent.storage import read_jsonl

    task, s = make_task(tmp_path, [
        AssistantTurn(content="开场"),
        AssistantTurn(content="第二问：结合代码说说实现？"),
        AssistantTurn(content="## 评估"),
    ])
    task.start()
    assert wait_until(lambda: s.state.value == "questioning")
    s.queue_repo_injection([RepoAnalysis(slug="alice/shop", status="ok", text="## 项目结构概述\nx")])
    task.submit_answer("我的回答")
    assert wait_until(lambda: any(m.get("content") == "第二问：结合代码说说实现？" for m in s.messages))
    idx_sys = next(i for i, m in enumerate(s.messages)
                   if m["role"] == "system" and "alice/shop" in m["content"])
    idx_user = next(i for i, m in enumerate(s.messages) if m.get("content") == "我的回答")
    assert idx_sys < idx_user
    assert any(e.get("role") == "note" and "GitHub" in e["content"] for e in read_jsonl(s.transcript_path))


def test_injection_dropped_if_interview_already_wrapping(tmp_path):
    from interview_agent.models import RepoAnalysis

    task, s = make_task(tmp_path, [
        AssistantTurn(content="开场"),
        AssistantTurn(content="## 评估"),
    ])
    task.start()
    assert wait_until(lambda: s.state.value == "questioning")
    s.to_wrapping()
    s.queue_repo_injection([RepoAnalysis(slug="a/b", status="ok", text="x")])
    task.submit_answer("回答")
    assert wait_until(lambda: task.ended)
    assert not any(m["role"] == "system" for m in s.messages[1:])


def test_compression_keeps_repo_analysis_and_emits_event(tmp_path):
    """触发紧急压缩后仓库分析被补回；注入时发布 github_ready 事件。"""
    from interview_agent.models import RepoAnalysis

    task, s = make_task(tmp_path, [
        AssistantTurn(content="开场"),
        AssistantTurn(content="第二问"),
        AssistantTurn(content="## 评估"),
    ])
    task.start()
    assert wait_until(lambda: s.state.value == "questioning")
    s.queue_repo_injection([RepoAnalysis(slug="alice/shop", status="ok", text="## 项目结构概述\nx")])
    import interview_agent.web.runner as runner_mod
    original = runner_mod.needs_emergency_offload
    try:
        runner_mod.needs_emergency_offload = lambda *a, **k: True
        task.submit_answer("我的回答")
        assert wait_until(lambda: any(m.get("content") == "第二问" for m in s.messages))
    finally:
        runner_mod.needs_emergency_offload = original
    assert any(m["role"] == "system" and "alice/shop" in m["content"] for m in s.messages)
    assert any(ev.get("status") == "github_ready" for ev in task.queue.snapshot())
