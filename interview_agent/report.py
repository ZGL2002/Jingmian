"""评估报告 Markdown 组装。"""
from __future__ import annotations


def build_report_markdown(session, eval_body: str) -> str:
    header = (
        "# 面试评估报告\n\n"
        f"- 会话：{session.session_id}\n"
        f"- 用户：{session.config.user_id}\n"
        f"- 题目数：{session.question_count}\n"
        f"- 语言：{session.config.language}\n\n"
    )
    return header + eval_body
