"""SSE 事件协议与线程安全可重放队列。"""
from __future__ import annotations
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


def sse_format(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class EventQueue:
    """事件列表 + 条件变量；新订阅者先重放再实时。"""

    def __init__(self, max_len: int = 500):
        self._events: list[dict] = []
        self._cond = threading.Condition()
        self._max_len = max_len

    def publish(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            if len(self._events) > self._max_len:
                del self._events[: len(self._events) - self._max_len]
            self._cond.notify_all()

    def snapshot(self) -> list[dict]:
        with self._cond:
            return list(self._events)

    def wait_for_events(self, after: int, timeout: float = 30.0) -> list[dict]:
        with self._cond:
            if len(self._events) <= after:
                self._cond.wait(timeout)
            return list(self._events)


def sse_stream(queue: EventQueue):
    """把事件队列转成 SSE 文本流；不主动终止，客户端关闭即停止。"""
    idx = 0
    while True:
        events = queue.wait_for_events(idx, timeout=30.0)
        for ev in events[idx:]:
            idx += 1
            yield sse_format(ev)
