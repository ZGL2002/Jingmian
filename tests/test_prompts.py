from interview_agent.prompts import (
    build_system_prompt, build_evaluation_messages, render_repo_section, render_resume_section,
)
from interview_agent.models import ResumeDocument, ResumeProject

def test_system_prompt_mentions_rules():
    p = build_system_prompt(None)
    assert "10" in p
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


def test_render_repo_section_contains_analysis():
    from interview_agent.models import RepoAnalysis
    section = render_repo_section([
        RepoAnalysis(slug="alice/shop", status="ok", text="## 项目结构概述\n单模块。\n## 建议提问点\n1. Redis 锁"),
    ])
    assert "GitHub 仓库" in section
    assert "alice/shop" in section
    assert "项目结构概述" in section
    assert "不符" in section  # 真实性验证指引


def test_render_repo_section_empty_when_all_skipped():
    from interview_agent.models import RepoAnalysis
    assert render_repo_section([RepoAnalysis(slug="alice/gone", status="skipped", reason="x")]) == ""


def test_render_repo_section_has_injection_defense():
    from interview_agent.models import RepoAnalysis
    section = render_repo_section([RepoAnalysis(slug="a/b", status="ok", text="分析正文")])
    assert "数据" in section and "忽略" in section


def test_system_prompt_pending_repos_hint():
    doc = ResumeDocument(raw_text="x", github_repos=["a/b"])
    p = build_system_prompt(doc, pending_repos=["a/b"])
    assert "后台" in p and "a/b" in p and "架构" in p
    assert build_system_prompt(doc).find("后台") == -1


def test_system_prompt_handles_supplement_marker():
    # 语音续说补发带「（补充上一题）」标记：面试官须识别为补充而非答非所问
    p = build_system_prompt(None)
    assert "（补充上一题）" in p
    assert "不算答非所问" in p
