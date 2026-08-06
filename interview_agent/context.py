"""上下文占用估算与压缩。"""
from __future__ import annotations


def estimate_chars(messages: list[dict]) -> int:
    return sum(len(str(m.get("content", ""))) for m in messages)


def needs_emergency_offload(messages: list[dict], limit: int, ratio: float) -> bool:
    return estimate_chars(messages) >= int(limit * ratio)


def build_sliding_window(messages: list[dict], keep_n: int, summary: str) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            out.append(m)
            break
    out.append({"role": "system", "content": f"（前文摘要）{summary}"})
    tail = [m for m in messages if m["role"] != "system"][-keep_n:]
    return out + tail
