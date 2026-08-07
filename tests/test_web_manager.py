import time
from interview_agent.web.manager import SessionManager
from interview_agent.llm import AssistantTurn, StreamEnd


class ManagerLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def make_manager(tmp_path, idle_timeout=1800, sweep_interval=60.0):
    cfg = {
        "session_root": str(tmp_path),
        "min_questions": 1,
        "language": "zh",
        "model": "deepseek-chat",
    }
    llm = ManagerLLM([
        AssistantTurn(content="开场"),
        AssistantTurn(content="问题一"),
        AssistantTurn(content="## 评估"),
        AssistantTurn(content="开场2"),
    ])
    return SessionManager(cfg, llm, idle_timeout=idle_timeout, sweep_interval=sweep_interval)


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_multi_session_isolation(tmp_path):
    m = make_manager(tmp_path)
    sid1 = m.start_session("alice", company="字节跳动", position="后端开发")
    sid2 = m.start_session("alice", company="腾讯", position="客户端开发")
    assert m.get_task("alice", sid1) is not m.get_task("alice", sid2)
    # 开场回合由后台线程异步执行：注册后立即返回，状态可能已推进到 questioning。
    assert m.snapshot("alice", sid1)["state"] in ("opening", "questioning")
    assert m.snapshot("alice", sid2)["state"] in ("opening", "questioning")


def test_start_with_resume_text(tmp_path):
    m = make_manager(tmp_path)
    sid = m.start_session("alice", resume_text="熟悉 Python，做过订单系统，用了 Redis")
    task = m.get_task("alice", sid)
    assert task is not None
    assert task.session.resume is not None
    assert "Redis" in task.session.resume.skills


def test_idle_sweep_removes_task(tmp_path):
    m = make_manager(tmp_path, idle_timeout=0.1, sweep_interval=0.05)
    sid = m.start_session("alice")
    assert wait_until(lambda: m.get_task("alice", sid) is None)


def test_submit_to_ended_task_gives_clear_error(tmp_path):
    m = make_manager(tmp_path)
    sid = m.start_session("alice")
    m.get_task("alice", sid).ended = True
    try:
        m.submit_answer("alice", sid, "x")
        assert False, "应当抛出 RuntimeError"
    except RuntimeError as e:
        assert "重新开始" in str(e)
