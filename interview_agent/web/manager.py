"""SessionManager：user_id → {session_id: InterviewTask} 两层注册表。"""
from __future__ import annotations
import threading
import time
from pathlib import Path
from ..agent import ToolAgent
from ..models import SessionConfig
from ..resume import parse_resume
from ..security import PathPolicy
from ..session import InterviewSession
from ..storage import read_jsonl
from ..tools import default_registry
from ..tools.base import ToolContext
from .events import EventQueue, snapshot_event
from .runner import InterviewTask


class SessionManager:
    def __init__(self, config: dict, llm, idle_timeout: float | None = None,
                 sweep_interval: float = 30.0):
        self.config = config
        self.llm = llm
        self.idle_timeout = idle_timeout or float(config.get("session_idle_timeout", 1800))
        self.sweep_interval = sweep_interval
        self._sessions: dict[str, dict[str, InterviewTask]] = {}
        self._lock = threading.Lock()
        self._sweeper = threading.Thread(target=self._sweep_loop, daemon=True, name="session-sweeper")
        self._sweeper.start()

    def start_session(self, user_id: str, *, company: str = "", position: str = "",
                      resume_text: str | None = None, jd_text: str = "",
                      experiences=None) -> str:
        if self.llm is None:
            raise RuntimeError("LLM 未初始化")
        resume = parse_resume(resume_text) if resume_text else None
        cfg = SessionConfig(
            user_id=user_id,
            session_root=Path(self.config["session_root"]),
            min_questions=int(self.config.get("min_questions", 20)),
            language=self.config.get("language", "zh"),
            model=self.config.get("model", "deepseek-chat"),
            company=company,
            position=position,
            jd_text=jd_text,
            experience_refs=list(experiences or []),
        )
        session = InterviewSession(cfg, resume)
        session.start()
        registry = default_registry()
        tool_ctx = ToolContext(
            user_id=user_id,
            session_dir=session.session_dir,
            policy=PathPolicy([session.session_dir]),
            transcript_path=session.transcript_path,
            wrap_allowed=session.can_auto_wrap,
        )
        agent = ToolAgent(self.llm, registry, session, tool_ctx)
        task = InterviewTask(user_id, session, agent, EventQueue(), self.idle_timeout)
        with self._lock:
            self._sessions.setdefault(user_id, {})[session.session_id] = task
        task.start()
        return session.session_id

    def get_task(self, user_id: str, session_id: str) -> InterviewTask | None:
        with self._lock:
            return self._sessions.get(user_id, {}).get(session_id)

    def submit_answer(self, user_id: str, session_id: str, text: str) -> None:
        task = self.get_task(user_id, session_id)
        if task is None:
            raise KeyError("会话不存在")
        if task.ended:
            raise RuntimeError("这场面试已结束（可能异常退出），请重新开始一场")
        task.submit_answer(text)

    def end_session(self, user_id: str, session_id: str) -> None:
        task = self.get_task(user_id, session_id)
        if task is None:
            raise KeyError("会话不存在")
        if task.ended:
            raise RuntimeError("这场面试已结束（可能异常退出），请重新开始一场")
        task.request_end()

    def snapshot(self, user_id: str, session_id: str) -> dict:
        task = self.get_task(user_id, session_id)
        if task is None:
            return {"state": "missing"}
        return {
            "state": task.session.state.value,
            "question_count": task.session.question_count,
            "busy": task.busy,
        }

    def snapshot_event(self, user_id: str, session_id: str) -> dict:
        return snapshot_event(self.snapshot(user_id, session_id))

    def _sweep_loop(self) -> None:
        while True:
            time.sleep(self.sweep_interval)
            now = time.time()
            with self._lock:
                for user_id, tasks in list(self._sessions.items()):
                    for sid, task in list(tasks.items()):
                        if task.ended:
                            del tasks[sid]
                        elif now - task.last_activity > self.idle_timeout:
                            task.shutdown.set()
                    if not tasks:
                        self._sessions.pop(user_id, None)
