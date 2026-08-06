import pytest
from interview_agent.security import PathPolicy, PathPolicyError, read_owner, check_owner, contains_secret

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

def test_contains_secret():
    assert contains_secret("key=sk-abc", "sk-abc")
    assert not contains_secret("hello", "sk-abc")
