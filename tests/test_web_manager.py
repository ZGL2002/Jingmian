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


def test_start_session_with_github_background_analysis(tmp_path, monkeypatch):
    """start_session 不再同步等待审读：开场提示词只有审读中引导，分析待 drain 注入。"""
    import base64, io, json as _json
    from interview_agent.repo_agent import start_repo_analysis

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/alice/shop"):
            payload = {"default_branch": "main", "description": "", "language": "Python", "stargazers_count": 0}
        elif "git/trees" in url:
            payload = {"tree": [{"path": "README.md", "type": "blob", "size": 8}]}
        elif "contents/README.md" in url:
            payload = {"content": base64.b64encode(b"# shop\n").decode(), "encoding": "base64"}
        else:
            raise AssertionError(f"意外请求: {url}")
        return FakeResponse(_json.dumps(payload).encode())

    monkeypatch.setattr("interview_agent.github.urlopen", fake_urlopen)
    monkeypatch.setattr(
        "interview_agent.web.manager.start_repo_analysis",
        lambda s, l, on_progress=None: start_repo_analysis(s, l, on_progress=on_progress, synchronous=True),
    )
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1,
        "language": "zh", "model": "deepseek-chat",
        "github_analysis_enabled": True, "github_token": "tk", "github_max_repos": 1,
    }
    llm = ManagerLLM([
        AssistantTurn(content="## 项目结构概述\n单模块"),   # 审读子 agent
        AssistantTurn(content="开场"),
        AssistantTurn(content="## 评估"),
    ])
    m = SessionManager(cfg, llm, idle_timeout=60, sweep_interval=60.0)
    sid = m.start_session("alice", resume_text="项目：商城 https://github.com/alice/shop")
    task = m.get_task("alice", sid)
    prompt = (task.session.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "后台" in prompt and "alice/shop" in prompt          # 审读中引导段
    assert "GitHub 仓库代码分析" not in prompt                   # 分析不阻塞进 prompt
    assert (task.session.session_dir / "repos" / "alice__shop" / "analysis.md").exists()
    assert task.session.config.github_token == "tk"
    # 同步审读完成得极快：runner 在开场轮次边界完成注入
    assert wait_until(lambda: any(
        m["role"] == "system" and "alice/shop" in m["content"] for m in task.session.messages
    ))
