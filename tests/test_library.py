import pytest
from interview_agent.library import (
    new_experience_id, save_experience, list_experiences, delete_experience, save_experience_ref,
)
from interview_agent.models import ExperienceEntry
from interview_agent.security import PathPolicyError
from interview_agent.storage import create_session_dir


def test_crud_roundtrip(tmp_path):
    e = ExperienceEntry(entry_id=new_experience_id(), title="字节面经", content="高频问缓存",
                        source="牛客", company="字节跳动", position="后端开发")
    p = save_experience(tmp_path, "alice", e)
    assert p.is_file()
    got = list_experiences(tmp_path, "alice")
    assert len(got) == 1
    assert got[0].company == "字节跳动"
    assert got[0].content == "高频问缓存"
    assert delete_experience(tmp_path, "alice", e.entry_id)
    assert list_experiences(tmp_path, "alice") == []


def test_owner_isolation_on_delete(tmp_path):
    e = ExperienceEntry(entry_id=new_experience_id(), title="t", content="c")
    save_experience(tmp_path, "alice", e)
    with pytest.raises(PathPolicyError):
        delete_experience(tmp_path, "bob", e.entry_id)


def test_delete_rejects_traversal(tmp_path):
    assert delete_experience(tmp_path, "alice", "../escape") is False


def test_reference_snapshot_written(tmp_path):
    sdir = create_session_dir(tmp_path, "alice")
    e = ExperienceEntry(entry_id="e1", title="字节面经", content="内容")
    p = save_experience_ref(sdir, "alice", 1, e)
    assert p.name == "1_字节面经.md"
    text = p.read_text(encoding="utf-8")
    assert "owner: alice" in text
    assert "内容" in text
