"""SSE 事件协议与线程安全可重放队列。"""
from __future__ import annotations
import asyncio
import json
import threading


def status_event(status: str) -> dict:
    return {"type": "status", "status": status}


def delta_event(text: str) -> dict:
    return {"type": "delta", "text": text}


def turn_end_event(question_count: int) -> dict:
    return {"type": "turn_end", "question_count": question_count}


def error_event(message: str) -> dict:
    return {"type": "error", "message": message}


def done_event(report_url: str) -> dict:
    return {"type": "status", "status": "done", "report_url": report_url}


def snapshot_event(snapshot: dict) -> dict:
    return {"type": "snapshot", **snapshot}


def sse_format(event: dict, seq: int | None = None) -> str:
    body = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    if seq is not None:
        return f"id: {seq}\n{body}"
    return body


class EventQueue:
    """事件列表 + 条件变量；新订阅者先重放再实时。"""

    def __init__(self, max_len: int = 500):
        self._events: list[dict] = []
        self._seqs: list[int] = []
        self._counter = 0
        self._cond = threading.Condition()
        self._max_len = max_len

    def publish(self, event: dict) -> None:
        with self._cond:
            self._counter += 1
            self._events.append(event)
            self._seqs.append(self._counter)
            if len(self._events) > self._max_len:
                del self._events[0]
                del self._seqs[0]
            self._cond.notify_all()

    def snapshot(self) -> list[dict]:
        with self._cond:
            return list(self._events)

    def wait_for_events(self, after: int, timeout: float = 30.0) -> list[dict]:
        with self._cond:
            if len(self._events) <= after:
                self._cond.wait(timeout)
            return list(self._events)

    def last_seq(self) -> int:
        with self._cond:
            return self._counter

    def events_after(self, last_seq: int) -> list[tuple[int, dict]]:
        """非阻塞返回 seq > last_seq 的事件列表。"""
        with self._cond:
            return [(s, ev) for s, ev in zip(self._seqs, self._events) if s > last_seq]

    def wait_for_events_after(self, last_seq: int, timeout: float = 30.0) -> list[tuple[int, dict]]:
        """阻塞直到出现 seq > last_seq 的事件；返回 [(seq, event), ...]。"""
        with self._cond:
            while self._counter <= last_seq:
                if not self._cond.wait(timeout):
                    break
            return [(s, ev) for s, ev in zip(self._seqs, self._events) if s > last_seq]


def sse_stream(queue: EventQueue, stop_when=None):
    """把事件队列转成 SSE 文本流；默认不主动终止，客户端关闭即停止。

    stop_when: 可选谓词，接收事件 dict；命中时先发出该事件再结束流。
    """
    idx = 0
    while True:
        events = queue.wait_for_events(idx, timeout=30.0)
        for ev in events[idx:]:
            idx += 1
            if stop_when is not None and stop_when(ev):
                yield sse_format(ev)
                return
            yield sse_format(ev)


async def sse_stream_async(queue: EventQueue, stop_when=None, last_id: int = 0):
    """异步版 SSE 流：短间隔轮询，不阻塞事件循环。

    采用短间隔轮询，不使用线程：既不会阻塞事件循环，也不会在
    Ctrl+C 关停时因线程池残留导致进程无法退出。
    last_id: 客户端已收到的最大事件序号；重连时只补发之后的事件。
    """
    seq = last_id
    while True:
        items = queue.events_after(seq)
        for s, ev in items:
            seq = s
            if stop_when is not None and stop_when(ev):
                yield sse_format(ev, seq)
                return
            yield sse_format(ev, seq)
        await asyncio.sleep(0.2)
