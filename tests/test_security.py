import pytest
from interview_agent.security import (
    PathPolicy, PathPolicyError, read_owner, check_owner,
    check_owner_if_marked, contains_secret,
)

def test_resolve_inside_root(tmp_path):
    policy = PathPolicy([tmp_path])
    p = policy.resolve("sub/file.txt")
    assert p == (tmp_path / "sub/file.txt").resolve()

def test_resolve_rejects_traversal(tmp_path):
    policy = PathPolicy([tmp_path])
    with pytest.raises(PathPolicyError):
        policy.resolve("../outside.txt")

def test_resolve_rejects_absolute_outside(tmp_path):
    policy = PathPolicy([tmp_path])
    with pytest.raises(PathPolicyError):
        policy.resolve(str(tmp_path.parent / "secret.txt"))

def test_resolve_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(outside)
    policy = PathPolicy([tmp_path])
    with pytest.raises(PathPolicyError):
        policy.resolve("link")

def test_owner_roundtrip_md(tmp_path):
    f = tmp_path / "t.md"
    f.write_text("<!-- owner: alice -->\nbody", encoding="utf-8")
    assert read_owner(f) == "alice"
    check_owner(f, "alice")

def test_owner_mismatch_jsonl(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"user_id": "bob"}\n', encoding="utf-8")
    with pytest.raises(PathPolicyError):
        check_owner(f, "alice")

def test_check_owner_if_marked_mismatch(tmp_path):
    f = tmp_path / "t.md"
    f.write_text("<!-- owner: bob -->\nbody", encoding="utf-8")
    with pytest.raises(PathPolicyError):
        check_owner_if_marked(f, "alice")

def test_check_owner_if_marked_unmarked_allowed(tmp_path):
    f = tmp_path / "scratch.txt"
    f.write_text("hello", encoding="utf-8")
    check_owner_if_marked(f, "alice")  # 无 owner 标记的草稿文件放行

def test_contains_secret():
    assert contains_secret("key=sk-abc", "sk-abc")
    assert not contains_secret("hello", "sk-abc")
