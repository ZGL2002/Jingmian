from pathlib import Path
from interview_agent.tools import default_registry
from interview_agent.tools.base import ToolContext
from interview_agent.security import PathPolicy
from interview_agent.storage import read_jsonl

def make_ctx(tmp_path, wrap_allowed=lambda: True):
    d = tmp_path / "s"
    d.mkdir(exist_ok=True)
    (d / "answers").mkdir(exist_ok=True)
    return ToolContext(
        user_id="alice", session_dir=d,
        policy=PathPolicy([tmp_path]), transcript_path=d / "t.jsonl",
        wrap_allowed=wrap_allowed,
    )

def test_registry_has_all_tools(tmp_path):
    reg = default_registry()
    names = {t["function"]["name"] for t in reg.schemas()}
    assert {
        "read_file", "write_file", "append_file", "edit_file",
        "list_dir", "append_transcript", "request_wrap",
    } <= names

def test_write_then_read(tmp_path):
    reg = default_registry()
    ctx = make_ctx(tmp_path)
    reg.execute("write_file", {"path": "s/a.txt", "content": "hello"}, ctx)
    assert reg.execute("read_file", {"path": "s/a.txt"}, ctx) == "hello"

def test_append_and_edit(tmp_path):
    reg = default_registry()
    ctx = make_ctx(tmp_path)
    reg.execute("write_file", {"path": "s/a.txt", "content": "abc"}, ctx)
    reg.execute("append_file", {"path": "s/a.txt", "content": "def"}, ctx)
    reg.execute("edit_file", {"path": "s/a.txt", "old_text": "bc", "new_text": "BC"}, ctx)
    assert reg.execute("read_file", {"path": "s/a.txt"}, ctx) == "aBCdef"

def test_edit_requires_unique_match(tmp_path):
    reg = default_registry()
    ctx = make_ctx(tmp_path)
    reg.execute("write_file", {"path": "s/a.txt", "content": "abcabc"}, ctx)
    out = reg.execute("edit_file", {"path": "s/a.txt", "old_text": "ab", "new_text": "x"}, ctx)
    assert out.startswith("错误：")

def test_list_dir(tmp_path):
    reg = default_registry()
    ctx = make_ctx(tmp_path)
    reg.execute("write_file", {"path": "s/a.txt", "content": "x"}, ctx)
    assert "a.txt" in reg.execute("list_dir", {"path": "s"}, ctx)

def test_append_transcript_note(tmp_path):
    reg = default_registry()
    ctx = make_ctx(tmp_path)
    reg.execute("append_transcript", {"content": "候选人说到一半，需要追问", "role": "note"}, ctx)
    entries = read_jsonl(ctx.transcript_path)
    assert entries[0]["role"] == "note"

def test_request_wrap_allowed_and_denied(tmp_path):
    reg = default_registry()
    ctx_ok = make_ctx(tmp_path, wrap_allowed=lambda: True)
    ctx_no = make_ctx(tmp_path, wrap_allowed=lambda: False)
    assert reg.execute("request_wrap", {}, ctx_ok) == "WRAP_REQUESTED"
    assert reg.execute("request_wrap", {}, ctx_no).startswith("错误：")

def test_unknown_tool_returns_error(tmp_path):
    reg = default_registry()
    out = reg.execute("nope", {}, make_ctx(tmp_path))
    assert out.startswith("错误：")

def test_traversal_blocked(tmp_path):
    reg = default_registry()
    out = reg.execute("read_file", {"path": "../secret.txt"}, make_ctx(tmp_path))
    assert out.startswith("错误：")
