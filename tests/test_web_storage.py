from interview_agent.storage import init_transcript, read_jsonl, list_sessions
from interview_agent.models import SessionConfig
from interview_agent.session import InterviewSession


def test_init_transcript_meta_company_position(tmp_path):
    path = tmp_path / "t.jsonl"
    init_transcript(path, "alice", "s1", company="字节跳动", position="后端开发")
    meta = read_jsonl(path)[0]
    assert meta["company"] == "字节跳动"
    assert meta["position"] == "后端开发"


def test_list_sessions_with_company_and_count(tmp_path):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1,
                        company="字节跳动", position="后端开发")
    s = InterviewSession(cfg)
    s.start()
    s.begin_questions()
    s.add_interviewer_message("问题一")
    s2 = InterviewSession(SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1))
    s2.start()
    sessions = list_sessions(tmp_path, "alice")
    assert len(sessions) == 2
    by_id = {item["session_id"]: item for item in sessions}
    first = by_id[s.session_id]
    assert first["company"] == "字节跳动"
    assert first["position"] == "后端开发"
    assert first["question_count"] == 1
    assert first["has_report"] is False
    assert by_id[s2.session_id]["company"] == ""


def test_list_sessions_skips_dirs_without_transcript(tmp_path):
    (tmp_path / "alice" / ".uploads").mkdir(parents=True)
    assert list_sessions(tmp_path, "alice") == []
