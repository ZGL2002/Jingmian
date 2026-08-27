# tests/test_style_plumbing.py
from interview_agent.models import SessionConfig
from interview_agent.prompts import build_system_prompt
from interview_agent.session import InterviewSession
from interview_agent.storage import read_jsonl


def test_prompt_contains_persona():
    p = build_system_prompt(None, persona="语气沉稳严厉，追问犀利")
    assert "语气沉稳严厉" in p


def test_prompt_without_persona_has_no_block():
    assert "风格人设" not in build_system_prompt(None)


def test_session_style_default_serious(tmp_path):
    cfg = SessionConfig(user_id="u1", session_root=tmp_path)
    s = InterviewSession(cfg)
    s.start()
    meta = read_jsonl(s.transcript_path)[0]
    assert meta["style"] == "serious"
    prompt = (s.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "严肃、专业" in prompt  # serious 人设片段


def test_session_style_cold(tmp_path):
    cfg = SessionConfig(user_id="u1", session_root=tmp_path, style="cold")
    s = InterviewSession(cfg)
    s.start()
    meta = read_jsonl(s.transcript_path)[0]
    assert meta["style"] == "cold"
    prompt = (s.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "公事公办" in prompt  # cold 人设片段
