"""评估流程：读记录 → 调模型（限流自动重试）→ 写报告。"""
from __future__ import annotations
import time

from .llm import LLMError
from .prompts import build_evaluation_messages
from .report import build_report_markdown
from .storage import read_jsonl, save_report


def transcript_to_text(session) -> str:
    lines = []
    for e in read_jsonl(session.transcript_path):
        if e["role"] in ("interviewer", "candidate"):
            lines.append(f"{e['role']}: {e['content']}")
    return "\n".join(lines)


def run_evaluation(session, llm):
    text = transcript_to_text(session)
    messages = build_evaluation_messages(text, session.config.language)
    last_error: LLMError | None = None
    for attempt in range(3):
        try:
            turn = llm.chat(messages)
            break
        except LLMError as e:  # noqa: BLE001 - 限流/瞬时失败重试，评估在后台线程不阻塞用户
            last_error = e
            time.sleep(min(getattr(e, "retry_after", None) or 0.5 * (2 ** attempt), 60.0))
    else:
        raise last_error
    md = build_report_markdown(session, turn.content or "评估未生成")
    return save_report(session.session_dir, md, session.config.user_id)
