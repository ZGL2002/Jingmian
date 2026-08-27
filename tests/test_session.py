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
