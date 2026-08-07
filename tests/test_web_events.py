import json
import asyncio
import threading
from interview_agent.web.events import (
    EventQueue, delta_event, turn_end_event, snapshot_event, sse_format, sse_stream,
    sse_stream_async, status_event,
)


def test_publish_snapshot_and_wait():
    q = EventQueue()
    q.publish(delta_event("a"))
    assert q.snapshot() == [{"type": "delta", "text": "a"}]
    assert len(q.wait_for_events(0, timeout=0.2)) == 1


def test_wait_returns_new_events():
    q = EventQueue()
    q.publish(delta_event("a"))
    t = threading.Thread(target=lambda: q.publish(turn_end_event(3)))
    t.start()
    t.join()
    events = q.wait_for_events(1, timeout=1.0)
    assert events[1] == {"type": "turn_end", "question_count": 3}


def test_cap_keeps_newest():
    q = EventQueue(max_len=3)
    for i in range(5):
        q.publish(delta_event(str(i)))
    assert [e["text"] for e in q.snapshot()] == ["2", "3", "4"]


def test_sse_format_and_snapshot_event():
    assert sse_format({"type": "x"}) == 'data: {"type": "x"}\n\n'
    ev = snapshot_event({"state": "questioning", "busy": False})
    assert ev["type"] == "snapshot"
    assert ev["state"] == "questioning"
    assert json.loads(sse_format(ev)[6:])["busy"] is False


def test_sse_stream_stops_on_done():
    q = EventQueue()
    q.publish(delta_event("a"))
    q.publish(status_event("done"))
    frames = list(sse_stream(q, stop_when=lambda e: e.get("status") == "done"))
    assert len(frames) == 2
    assert frames[-1] == sse_format(status_event("done"))


def test_sse_stream_async_yields_and_terminates():
    q = EventQueue()
    q.publish(delta_event("a"))
    q.publish(status_event("done"))

    async def collect():
        out = []
        async for f in sse_stream_async(q, stop_when=lambda e: e.get("status") == "done"):
            out.append(f)
        return out

    frames = asyncio.run(collect())
    assert [json.loads(f[6:]) for f in frames] == [
        {"type": "delta", "text": "a"},
        {"type": "status", "status": "done"},
    ]


def test_sse_stream_async_wait_does_not_block_event_loop():
    """空队列等待期间，事件循环上的其他任务必须继续运转。"""
    q = EventQueue()

    async def ticker():
        ticks = 0
        for _ in range(20):
            ticks += 1
            await asyncio.sleep(0.01)
        return ticks

    async def consume():
        out = []
        async for f in sse_stream_async(q, stop_when=lambda e: e.get("status") == "done"):
            out.append(f)
        return out

    async def main():
        t = asyncio.create_task(ticker())
        c = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        q.publish(status_event("done"))
        frames = await c
        ticks = await t
        return frames, ticks

    frames, ticks = asyncio.run(main())
    assert len(frames) == 1
    assert ticks >= 3, "事件循环在 SSE 等待期间被阻塞"
