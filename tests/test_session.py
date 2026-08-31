from interview_agent.session import InterviewSession
from interview_agent.models import SessionConfig, SessionState, ResumeDocument
from interview_agent.storage import read_jsonl

def make_session(tmp_path, resume=None, **kw):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, **kw)
    return InterviewSession(cfg, resume)

def test_start_creates_files(tmp_path):
    s = make_session(tmp_path)
    s.start()
    assert s.state == SessionState.OPENING
    assert (s.session_dir / "prompt.md").exists()
    meta = read_jsonl(s.transcript_path)[0]
    assert meta == {"role": "meta", "user_id": "alice", "session_id": s.session_id, "style": "serious"}

def test_start_writes_resume_copy(tmp_path):
    s = make_session(tmp_path, ResumeDocument(raw_text="简历"))
    s.start()
    text = (s.session_dir / "resume.md").read_text(encoding="utf-8")
    assert text.startswith("<!-- owner: alice -->")
    assert text.endswith("简历")

def test_prompt_uses_configured_min_questions(tmp_path):
    s = make_session(tmp_path, min_questions=3)
    s.start()
    prompt = (s.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "至少完成 3 题" in prompt

def test_empty_candidate_message_ignored(tmp_path):
    s = make_session(tmp_path)
    s.start()
    s.begin_questions()
    s.add_candidate_message("")
    s.add_candidate_message("   ")
    entries = read_jsonl(s.transcript_path)
    assert all(e["role"] != "candidate" for e in entries)
    assert len(s.messages) == 1  # 只保留 system 消息

def test_question_counting(tmp_path):
    s = make_session(tmp_path)
    s.start()
    s.begin_questions()
    s.add_interviewer_message("介绍一下你的项目？")
    s.add_interviewer_message("追问：为什么用 Redis？")
    assert s.question_count == 2

def test_wrap_guard(tmp_path):
    s = make_session(tmp_path, min_questions=3)
    s.start()
    s.begin_questions()
    assert not s.can_auto_wrap()
    for i in range(3):
        s.add_interviewer_message(f"q{i}")
    assert s.can_auto_wrap()

def test_long_answer_offload(tmp_path):
    s = make_session(tmp_path, answer_offload_threshold=10)
    s.start()
    s.begin_questions()
    s.add_candidate_message("长" * 50)
    last = read_jsonl(s.transcript_path)[-1]
    assert last["role"] == "candidate"
    assert last["ref"] == "answer_1.md"
    assert len(last["content"]) < 30
    assert (s.session_dir / "answers" / "answer_1.md").exists()
    assert s.messages[-1]["role"] == "user"
    assert "answer_1.md" in s.messages[-1]["content"]


def test_pending_repos_hint_in_prompt(tmp_path):
    from interview_agent.models import ResumeDocument

    cfg = SessionConfig(user_id="alice", session_root=tmp_path)
    s = InterviewSession(cfg, ResumeDocument(raw_text="x", github_repos=["alice/shop"]))
    s.pending_repos = ["alice/shop"]
    s.start()
    p = s.messages[0]["content"]
    assert "后台" in p and "alice/shop" in p
    assert "架构" in p  # 引导先问整体架构


def test_queue_and_drain_repo_injection(tmp_path):
    from interview_agent.models import RepoAnalysis

    s = make_session(tmp_path)
    s.start()
    s.begin_questions()
    s.add_interviewer_message("第一问")
    s.queue_repo_injection([RepoAnalysis(slug="alice/shop", status="ok", text="## 项目结构概述\nx")])
    text = s.drain_repo_injection()
    assert text and "alice/shop" in text and "系统资料更新" in text
    assert s.messages[-1] == {"role": "system", "content": text}
    assert any(e.get("role") == "note" for e in read_jsonl(s.transcript_path))
    assert s.drain_repo_injection() is None  # 只注入一次


def test_queue_marks_skipped_repos_in_injection(tmp_path):
    from interview_agent.models import RepoAnalysis

    s = make_session(tmp_path)
    s.start()
    s.begin_questions()
    s.queue_repo_injection([
        RepoAnalysis(slug="a/b", status="ok", text="分析"),
        RepoAnalysis(slug="c/d", status="skipped", reason="网络错误"),
    ])
    text = s.drain_repo_injection()
    assert "a/b" in text and "c/d" in text and "网络错误" in text


def test_drain_dropped_when_interview_wrapping(tmp_path):
    from interview_agent.models import RepoAnalysis

    s = make_session(tmp_path)
    s.start()
    s.begin_questions()
    s.to_wrapping()
    s.queue_repo_injection([RepoAnalysis(slug="a/b", status="ok", text="x")])
    assert s.drain_repo_injection() is None
    assert not any(m["role"] == "system" for m in s.messages[1:])


def test_reapply_repo_analysis_after_compression(tmp_path):
    """上下文压缩会丢弃中段 system 消息：压缩后必须能把仓库分析补回对话。"""
    from interview_agent.context import build_sliding_window
    from interview_agent.models import RepoAnalysis

    s = make_session(tmp_path)
    s.start()
    s.begin_questions()
    s.add_interviewer_message("第一问")
    s.queue_repo_injection([RepoAnalysis(slug="a/b", status="ok", text="分析正文")])
    assert s.drain_repo_injection()
    s.messages = build_sliding_window(s.messages, 2, "压缩")
    assert not any("分析正文" in str(m.get("content")) for m in s.messages)
    s.reapply_repo_analysis()
    assert any(m["role"] == "system" and "分析正文" in m["content"] for m in s.messages)


def test_reapply_noop_without_analyses(tmp_path):
    s = make_session(tmp_path)
    s.start()
    before = len(s.messages)
    s.reapply_repo_analysis()
    assert len(s.messages) == before
