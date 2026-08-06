"""评估流程：读记录 → 调模型 → 写报告。"""
from __future__ import annotations
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
    turn = llm.chat(build_evaluation_messages(text, session.config.language))
    md = build_report_markdown(session, turn.content or "评估未生成")
    return save_report(session.session_dir, md, session.config.user_id)
