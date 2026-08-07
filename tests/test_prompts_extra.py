from interview_agent.prompts import build_system_prompt
from interview_agent.models import ExperienceEntry


def test_company_position_and_jd_sections():
    p = build_system_prompt(None, company="字节跳动", position="后端开发", jd_text="熟悉 Go 与高并发")
    assert "目标岗位：字节跳动 后端开发" in p
    assert "岗位描述（JD）" in p
    assert "熟悉 Go 与高并发" in p


def test_experience_refs_section():
    refs = [ExperienceEntry(entry_id="e1", title="字节面经", content="高频问 Redis 缓存穿透", source="牛客")]
    p = build_system_prompt(None, experience_refs=refs)
    assert "参考面经" in p
    assert "高频问 Redis 缓存穿透" in p


def test_without_extras_unchanged():
    p = build_system_prompt(None)
    assert "目标岗位" not in p
    assert "岗位描述" not in p
    assert "参考面经" not in p
