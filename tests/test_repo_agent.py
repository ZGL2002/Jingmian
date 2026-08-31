"""仓库分析子 agent 测试：假 LLM + 假抓取客户端驱动完整循环与降级。"""
import json

from interview_agent.github import GitHubError
from interview_agent.llm import AssistantTurn, ToolCall, LLMError
from interview_agent.resume import parse_resume
from interview_agent.repo_agent import analyze_resume_repos, start_repo_analysis


class ScriptedLLM:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.last_messages = None

    def chat(self, messages, tools=None, max_tokens=None):
        self.calls.append((len(messages), tools is not None))
        self.last_messages = messages
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


class FakeClient:
    def __init__(self, meta=None, tree=None, files=None, error=None):
        self.meta = meta or {
            "slug": "alice/shop", "default_branch": "main",
            "description": "商城", "language": "Python", "stars": 3,
        }
        self.tree = tree if tree is not None else ["README.md", "src/app.py"]
        self.files = files or {"README.md": "# shop\n说明", "src/app.py": "print(1)"}
        self.error = error

    def fetch_repo(self, slug):
        if self.error:
            raise self.error
        return self.meta

    def fetch_tree(self, slug, ref):
        if self.error:
            raise self.error
        return self.tree

    def fetch_file(self, slug, ref, path):
        if path not in self.files:
            raise GitHubError(f"文件不存在（404）: {path}")
        return self.files[path]


RESUME = parse_resume(
    "项目经历\n项目A：商城后端 https://github.com/alice/shop\n"
    "Python + Redis 高并发下单\n个人项目 https://github.com/bob/demo"
)
ANALYSIS = (
    "## 项目结构概述\n单模块 Flask。\n## 技术栈实证\nFlask、Redis。\n"
    "## 核心实现亮点\napp.py 中用 Redis 锁。\n## 与简历描述对照\n吻合。\n"
    "## 建议提问点\n1. Redis 锁如何避免超卖？（依据 src/app.py）"
)


def find_tool_result(messages, call_id):
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id:
            return m["content"]
    return None


def test_analyze_ok_writes_cache_and_analysis(tmp_path):
    llm = ScriptedLLM([
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t1", name="list_repo_files", arguments={})]),
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t2", name="read_repo_file", arguments={"path": "README.md"})]),
        AssistantTurn(content=ANALYSIS),
        AssistantTurn(content=ANALYSIS),  # bob/demo 直接输出分析
    ])
    results = analyze_resume_repos(
        RESUME, llm=llm, session_dir=tmp_path, user_id="alice", client=FakeClient(),
    )
    assert [r.status for r in results] == ["ok", "ok"]
    ok = results[0]
    assert ok.slug == "alice/shop"
    assert "项目结构概述" in ok.text
    d = tmp_path / "repos" / "alice__shop"
    assert (d / "tree.txt").read_text(encoding="utf-8").splitlines() == ["README.md", "src/app.py"]
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "ok" and meta["ref"] == "main"
    assert (d / "files" / "README.md").read_text(encoding="utf-8").startswith("# shop")
    assert "alice" in (d / "analysis.md").read_text(encoding="utf-8").splitlines()[0]
    assert llm.calls[0][1] is True  # 子 agent 拿到了工具 schema


def test_analyze_enforces_read_limit_and_rejects_unknown_path(tmp_path):
    llm = ScriptedLLM([
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t1", name="read_repo_file", arguments={"path": "README.md"})]),
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t2", name="read_repo_file", arguments={"path": "README.md"})]),
        AssistantTurn(content=None, tool_calls=[ToolCall(id="t3", name="read_repo_file", arguments={"path": "hack.py"})]),
        AssistantTurn(content=ANALYSIS),
    ])
    analyze_resume_repos(
        RESUME, llm=llm, session_dir=tmp_path, user_id="alice",
        client=FakeClient(), max_files_read=1, max_repos=1,
    )
    messages = llm.last_messages
    assert find_tool_result(messages, "t1").startswith("# shop")
    assert "上限" in find_tool_result(messages, "t2")
    assert "不在仓库文件列表" in find_tool_result(messages, "t3")
    assert not (tmp_path / "repos" / "alice__shop" / "files" / "hack.py").exists()


def test_analyze_skips_on_fetch_error_and_records_reason(tmp_path):
    llm = ScriptedLLM([])
    results = analyze_resume_repos(
        RESUME, llm=llm, session_dir=tmp_path, user_id="alice",
        client=FakeClient(error=GitHubError("网络错误，无法访问 GitHub: refused")),
    )
    assert all(r.status == "skipped" for r in results)
    assert "网络" in results[0].reason
    assert llm.calls == []  # 抓取失败不进入 LLM 循环
    meta = json.loads((tmp_path / "repos" / "alice__shop" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "skipped"


def test_analyze_skips_on_llm_error(tmp_path):
    llm = ScriptedLLM([LLMError("LLM 调用失败: x"), LLMError("LLM 调用失败: x"), LLMError("LLM 调用失败: x")])
    results = analyze_resume_repos(
        RESUME, llm=llm, session_dir=tmp_path, user_id="alice", client=FakeClient(),
        max_repos=1,
    )
    assert results[0].status == "skipped"
    assert "LLM" in results[0].reason


def test_analyze_no_repos_returns_empty(tmp_path):
    resume = parse_resume("熟悉 Python，无链接项目")
    assert analyze_resume_repos(
        resume, llm=ScriptedLLM([]), session_dir=tmp_path, user_id="alice", client=FakeClient(),
    ) == []
    assert analyze_resume_repos(
        None, llm=ScriptedLLM([]), session_dir=tmp_path, user_id="alice", client=FakeClient(),
    ) == []


def test_analyze_respects_max_repos_and_reports_progress(tmp_path):
    seen = []
    llm = ScriptedLLM([AssistantTurn(content=ANALYSIS), AssistantTurn(content=ANALYSIS)])
    results = analyze_resume_repos(
        RESUME, llm=llm, session_dir=tmp_path, user_id="alice", client=FakeClient(),
        max_repos=2, on_progress=lambda slug, i, n, msg: seen.append((slug, i, n, msg)),
    )
    assert [r.slug for r in results] == ["alice/shop", "bob/demo"]
    assert seen[0][1] == 1 and seen[-1][1] == 2 and seen[0][2] == 2
    assert any("alice/shop" in s[0] for s in seen)


def test_analysis_text_capped_for_prompt(tmp_path):
    long_analysis = "## 项目结构概述\n" + "细节。" * 5000
    llm = ScriptedLLM([AssistantTurn(content=long_analysis)])
    results = analyze_resume_repos(
        RESUME, llm=llm, session_dir=tmp_path, user_id="alice", client=FakeClient(), max_repos=1,
    )
    assert len(results[0].text) < len(long_analysis)
    assert "完整分析" in results[0].text


def test_start_disabled_returns_none_without_pending(tmp_path):
    from interview_agent.models import SessionConfig
    from interview_agent.session import InterviewSession
    from interview_agent.repo_agent import start_repo_analysis

    cfg = SessionConfig(user_id="alice", session_root=tmp_path, github_analysis_enabled=False)
    s = InterviewSession(cfg, RESUME)
    llm = ScriptedLLM([])
    assert start_repo_analysis(s, llm, client=FakeClient()) is None
    assert s.pending_repos == []
    assert llm.calls == []


def test_start_sync_sets_pending_and_queues_injection(tmp_path):
    from interview_agent.models import SessionConfig
    from interview_agent.session import InterviewSession
    from interview_agent.repo_agent import start_repo_analysis

    s = InterviewSession(SessionConfig(user_id="alice", session_root=tmp_path, github_max_repos=2), RESUME)
    llm = ScriptedLLM([AssistantTurn(content=ANALYSIS), AssistantTurn(content=ANALYSIS)])
    assert start_repo_analysis(s, llm, client=FakeClient(), synchronous=True) is None
    assert s.pending_repos == ["alice/shop", "bob/demo"]
    s.start()
    assert "后台" in s.messages[0]["content"] and "alice/shop" in s.messages[0]["content"]
    s.begin_questions()
    text = s.drain_repo_injection()
    assert text and "alice/shop" in text and "bob/demo" in text


def test_start_async_runs_in_daemon_thread_and_queues(tmp_path):
    from interview_agent.models import SessionConfig
    from interview_agent.session import InterviewSession
    from interview_agent.repo_agent import start_repo_analysis

    s = InterviewSession(SessionConfig(user_id="alice", session_root=tmp_path, github_max_repos=2), RESUME)
    llm = ScriptedLLM([AssistantTurn(content=ANALYSIS), AssistantTurn(content=ANALYSIS)])
    thread = start_repo_analysis(s, llm, client=FakeClient())
    assert thread is not None and thread.daemon
    thread.join(timeout=5)
    s.start()
    s.begin_questions()
    assert s.drain_repo_injection() is not None


def test_request_budget_is_per_repo(tmp_path, monkeypatch):
    """单仓预算：每仓新建 client，3 仓互不挤占。"""
    created = []

    class CountingClient(FakeClient):
        def __init__(self, *a, token: str = "", **kw):
            super().__init__(*a, **kw)
            created.append(self)

    monkeypatch.setattr("interview_agent.repo_agent.GitHubClient", CountingClient)
    llm = ScriptedLLM([AssistantTurn(content=ANALYSIS), AssistantTurn(content=ANALYSIS)])
    results = analyze_resume_repos(RESUME, llm=llm, session_dir=tmp_path, user_id="alice", max_repos=2)
    assert [r.status for r in results] == ["ok", "ok"]
    assert len(created) == 2


def test_analysis_runs_serialized_across_sessions(tmp_path, monkeypatch):
    """进程内审读串行执行：多会话并行开面时不会同时压上 LLM/GitHub 限流。"""
    import threading
    import time as _time

    active = [0]
    peak = [0]
    lock = threading.Lock()

    def fake_analyze(resume, **kw):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        _time.sleep(0.15)
        with lock:
            active[0] -= 1
        return []

    monkeypatch.setattr("interview_agent.repo_agent.analyze_resume_repos", fake_analyze)
    from interview_agent.models import SessionConfig
    from interview_agent.session import InterviewSession

    threads = []
    for i in range(3):
        s = InterviewSession(SessionConfig(user_id=f"u{i}", session_root=tmp_path), RESUME)
        t = start_repo_analysis(s, ScriptedLLM([]))
        threads.append(t)
    for t in threads:
        assert t is not None
        t.join(timeout=5)
    assert peak[0] == 1
