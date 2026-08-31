from interview_agent.storage import (
    create_session_dir, atomic_write, append_jsonl, read_jsonl,
    init_transcript, offload_long_answer, save_report,
)

def test_create_session_dir(tmp_path):
    d = create_session_dir(tmp_path, "alice")
    assert d.parent.name == "alice"
    assert (d / "answers").is_dir()
    assert (d / "blocks").is_dir()
    parts = d.name.split("_")
    assert len(parts) == 3
    assert len(parts[-1]) == 8

def test_atomic_write_no_tmp_left(tmp_path):
    p = tmp_path / "a.md"
    atomic_write(p, "x")
    assert p.read_text(encoding="utf-8") == "x"
    assert not p.with_name("a.md.tmp").exists()

def test_append_and_read_jsonl(tmp_path):
    p = tmp_path / "t.jsonl"
    append_jsonl(p, {"a": 1})
    append_jsonl(p, {"b": 2})
    assert read_jsonl(p) == [{"a": 1}, {"b": 2}]

def test_init_transcript_meta(tmp_path):
    p = tmp_path / "t.jsonl"
    init_transcript(p, "alice", "s1")
    assert read_jsonl(p)[0] == {"role": "meta", "user_id": "alice", "session_id": "s1"}

def test_offload_and_report(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    a = offload_long_answer(d, 1, "长文", "alice")
    assert a.name == "answer_1.md"
    a_text = a.read_text(encoding="utf-8")
    assert a_text.startswith("<!-- owner: alice -->")
    assert a_text.endswith("长文")
    r = save_report(d, "# 报告", "alice")
    assert r.name == "report.md"
    assert r.read_text(encoding="utf-8").startswith("<!-- owner: alice -->")


def test_atomic_write_concurrent_same_path_no_errors(tmp_path):
    """跨线程写同一路径（后台审读缓存 vs 面试中工具缓存）不得抛异常。"""
    import threading

    from interview_agent.storage import atomic_write

    p = tmp_path / "f.txt"
    errors: list = []

    def write_many(tag: str) -> None:
        try:
            for i in range(100):
                atomic_write(p, f"{tag}-{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t1 = threading.Thread(target=write_many, args=("a",))
    t2 = threading.Thread(target=write_many, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert errors == []
    assert p.read_text(encoding="utf-8").startswith(("a-", "b-"))
