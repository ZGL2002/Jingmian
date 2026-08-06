from interview_agent.evaluate import run_evaluation, transcript_to_text
from interview_agent.report import build_report_markdown
from interview_agent.session import InterviewSession
from interview_agent.models import SessionConfig
from interview_agent.llm import AssistantTurn

class OneShotLLM:
    def chat(self, messages, tools=None, max_tokens=None):
        assert tools is None
        return AssistantTurn(content="## 技术准确性\n8 分。")

def make_session(tmp_path):
    cfg = SessionConfig(user_id="alice", session_root=tmp_path, min_questions=1)
    s = InterviewSession(cfg)
    s.start()
    s.begin_questions()
    s.add_interviewer_message("q1")
    s.add_candidate_message("a1")
    return s

def test_transcript_to_text(tmp_path):
    s = make_session(tmp_path)
    text = transcript_to_text(s)
    assert "interviewer: q1" in text
    assert "candidate: a1" in text

def test_build_report_header(tmp_path):
    s = make_session(tmp_path)
    md = build_report_markdown(s, "评估正文")
    assert "# 面试评估报告" in md
    assert s.session_id in md
    assert "alice" in md

def test_run_evaluation_writes_report(tmp_path):
    s = make_session(tmp_path)
    path = run_evaluation(s, OneShotLLM())
    assert path.name == "report.md"
    assert "## 技术准确性" in path.read_text(encoding="utf-8")
