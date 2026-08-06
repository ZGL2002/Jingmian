from pathlib import Path
from interview_agent.models import SessionState, ResumeDocument, SessionConfig, ResumeProject

def test_session_state_values():
    assert SessionState.QUESTIONING.value == "questioning"
    assert SessionState.DONE.value == "done"

def test_resume_document_defaults():
    doc = ResumeDocument(raw_text="hello")
    assert doc.languages == []
    assert doc.skills == []
    assert doc.projects == []
    assert doc.summary == ""

def test_resume_project_defaults():
    p = ResumeProject(name="订单系统")
    assert p.tech_stack == []

def test_session_config_defaults(tmp_path):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path)
    assert cfg.min_questions == 20
    assert cfg.answer_offload_threshold == 100_000
    assert cfg.context_safety_ratio == 0.8
    assert cfg.max_context_chars == 60_000
    assert cfg.language == "zh"
