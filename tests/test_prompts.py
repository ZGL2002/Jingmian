from interview_agent.prompts import build_system_prompt, build_evaluation_messages, render_resume_section
from interview_agent.models import ResumeDocument, ResumeProject

def test_system_prompt_mentions_rules():
    p = build_system_prompt(None)
    assert "20" in p
    assert "场景题" in p
    assert "request_wrap" in p
    assert "数据" in p

def test_system_prompt_differs_by_resume():
    a = build_system_prompt(None)
    doc = ResumeDocument(raw_text="熟悉 Go", languages=["Go"], skills=["Redis"])
    b = build_system_prompt(doc)
    assert a != b
    assert "Go" in b

def test_resume_section_contains_details():
    doc = ResumeDocument(
        raw_text="x", languages=["Python"], skills=["Redis"],
        projects=[ResumeProject(name="订单系统", description="高并发", tech_stack=["Python"])],
    )
    s = render_resume_section(doc)
    assert "Python" in s
    assert "订单系统" in s
    assert "Redis" in s

def test_evaluation_messages_include_transcript():
    msgs = build_evaluation_messages("interviewer: 你好\ncandidate: 你好")
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "interviewer: 你好\ncandidate: 你好"

def test_system_prompt_uses_min_questions():
    p = build_system_prompt(None, min_questions=5)
    assert "至少完成 5 题" in p
    assert "至少完成 20 题" not in p
