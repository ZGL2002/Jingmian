from interview_agent.context import estimate_chars, needs_emergency_offload, build_sliding_window

def test_estimate_chars():
    msgs = [{"role": "user", "content": "abc"}, {"role": "assistant", "content": "def"}]
    assert estimate_chars(msgs) == 6

def test_emergency_offload_flag():
    hot = [{"role": "user", "content": "x" * 49_000}]
    cool = [{"role": "user", "content": "x" * 10_000}]
    assert needs_emergency_offload(hot, limit=60_000, ratio=0.8)
    assert not needs_emergency_offload(cool, limit=60_000, ratio=0.8)

def test_sliding_window_keeps_system_and_recent():
    msgs = [{"role": "system", "content": "sys"}] + [{"role": "user", "content": f"m{i}"} for i in range(10)]
    out = build_sliding_window(msgs, keep_n=4, summary="摘要内容")
    assert out[0] == {"role": "system", "content": "sys"}
    assert any(m["role"] == "system" and "摘要内容" in m["content"] for m in out)
    assert len(out) == 1 + 1 + 4
    assert out[-1]["content"] == "m9"
