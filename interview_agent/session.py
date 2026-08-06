"""面试会话状态机与对话记录。"""
from __future__ import annotations
from .models import SessionConfig, SessionState, ResumeDocument
from .prompts import build_system_prompt
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
        self.state = SessionState.READY
        self.question_count = 0
        self.session_dir = create_session_dir(config.session_root, config.user_id)
        self.session_id = self.session_dir.name
        self.transcript_path = self.session_dir / "transcript.jsonl"
        self.messages: list[dict] = []
        self._answer_index = 0

    def start(self) -> None:
        init_transcript(self.transcript_path, self.config.user_id, self.session_id)
        prompt = build_system_prompt(
            self.resume, self.config.language, min_questions=self.config.min_questions
        )
        write_owned(self.session_dir / "prompt.md", self.config.user_id, prompt)
        if self.resume is not None:
            write_owned(self.session_dir / "resume.md", self.config.user_id, self.resume.raw_text)
        self.messages = [{"role": "system", "content": prompt}]
        self.state = SessionState.OPENING

    def begin_questions(self) -> None:
        self.state = SessionState.QUESTIONING

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
