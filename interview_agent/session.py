"""面试会话状态机与对话记录。"""
from __future__ import annotations
from .models import SessionConfig, SessionState, ResumeDocument
from .persona import get_style
from .prompts import build_system_prompt, render_repo_section
from .library import save_experience_ref
from .storage import (
    append_jsonl, create_session_dir, init_transcript, offload_long_answer,
    timestamp, write_owned,
)


def _entry(role: str, content: str, **kw) -> dict:
    return {"role": role, "content": content, "timestamp": timestamp(), **kw}


class InterviewSession:
    def __init__(self, config: SessionConfig, resume: ResumeDocument | None = None):
        self.config = config
        self.resume = resume
        self.pending_repos: list[str] = []      # 后台审读中的仓库（start() 前设置，进入开场提示词）
        self.repo_analyses: list = []           # 审读结果（queue_repo_injection 时更新）
        self._pending_repo_injection: str | None = None
        self.state = SessionState.READY
        self.question_count = 0
        self.session_dir = create_session_dir(config.session_root, config.user_id)
        self.session_id = self.session_dir.name
        self.transcript_path = self.session_dir / "transcript.jsonl"
        self.messages: list[dict] = []
        self._answer_index = 0

    def start(self) -> None:
        init_transcript(
            self.transcript_path,
            self.config.user_id,
            self.session_id,
            company=self.config.company,
            position=self.config.position,
            style=self.config.style,
        )
        prompt = build_system_prompt(
            self.resume,
            self.config.language,
            min_questions=self.config.min_questions,
            company=self.config.company,
            position=self.config.position,
            jd_text=self.config.jd_text or None,
            experience_refs=self.config.experience_refs,
            persona=get_style(self.config.style).prompt_fragment,
            pending_repos=self.pending_repos,
        )
        write_owned(self.session_dir / "prompt.md", self.config.user_id, prompt)
        if self.resume is not None:
            write_owned(self.session_dir / "resume.md", self.config.user_id, self.resume.raw_text)
        if self.config.jd_text:
            write_owned(self.session_dir / "jd.md", self.config.user_id, self.config.jd_text)
        for i, ref in enumerate(self.config.experience_refs, 1):
            save_experience_ref(self.session_dir, self.config.user_id, i, ref)
        self.messages = [{"role": "system", "content": prompt}]
        self.state = SessionState.OPENING

    def begin_questions(self) -> None:
        self.state = SessionState.QUESTIONING

    def queue_repo_injection(self, analyses: list) -> None:
        """后台审读线程完成时调用：暂存注入文本（只做属性赋值，线程安全）。

        真正写入 messages 由轮次循环在安全时机调用 drain_repo_injection 完成，
        避免打断 assistant tool_calls 与其结果之间的消息顺序。
        """
        self.repo_analyses = list(analyses)
        ok = [a for a in analyses if a.status == "ok" and a.text]
        skipped = [a for a in analyses if a.status != "ok"]
        if not ok and not skipped:
            return
        parts = ["（系统资料更新）候选人 GitHub 仓库代码审读已完成，供后续提问使用。"]
        section = render_repo_section(ok)
        if section:
            parts.append(section)
        if skipped:
            reasons = "；".join(f"{a.slug}（{a.reason}）" for a in skipped)
            parts.append(f"以下仓库审读失败已跳过，按简历正常提问：{reasons}")
        self._pending_repo_injection = "\n\n".join(parts)

    def drain_repo_injection(self) -> str | None:
        """轮次边界调用：把待注入的仓库分析写入对话；面试已收尾则丢弃。"""
        text = self._pending_repo_injection
        if not text:
            return None
        self._pending_repo_injection = None
        if self.state not in (SessionState.OPENING, SessionState.QUESTIONING):
            return None
        self.messages.append({"role": "system", "content": text})
        append_jsonl(self.transcript_path, _entry("note", "GitHub 仓库代码审读完成，已注入面试官资料"))
        return text

    def reapply_repo_analysis(self) -> None:
        """上下文压缩只保留首条 system 消息：压缩后调用，把仓库分析补回对话。"""
        section = render_repo_section([a for a in self.repo_analyses if a.status == "ok" and a.text])
        if section:
            self.messages.append({"role": "system", "content": section})

    def add_candidate_message(self, text: str) -> None:
        if not text.strip():
            return
        if len(text) > self.config.answer_offload_threshold:
            self._answer_index += 1
            path = offload_long_answer(self.session_dir, self._answer_index, text, self.config.user_id)
            excerpt = text[:200]
            self.messages.append({
                "role": "user",
                "content": excerpt + f"\n…（全文已保存到 answers/{path.name}，需要时用 read_file 读取）",
            })
            note = f"长回答已落盘：{path.name}"
            append_jsonl(self.transcript_path, _entry("candidate", note, ref=path.name))
        else:
            self.messages.append({"role": "user", "content": text})
            append_jsonl(self.transcript_path, _entry("candidate", text))

    def add_interviewer_message(self, content: str) -> None:
        if self.state == SessionState.QUESTIONING:
            self.question_count += 1
        append_jsonl(
            self.transcript_path,
            _entry("interviewer", content, question_number=self.question_count),
        )
        self.messages.append({"role": "assistant", "content": content})

    def can_auto_wrap(self) -> bool:
        return self.question_count >= self.config.min_questions

    def to_wrapping(self) -> None:
        self.state = SessionState.WRAPPING

    def to_evaluating(self) -> None:
        self.state = SessionState.EVALUATING

    def to_done(self) -> None:
        self.state = SessionState.DONE
