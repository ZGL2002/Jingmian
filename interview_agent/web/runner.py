"""后台面试循环：开场 → 回答回合 → 结束评估，事件推送。"""
from __future__ import annotations
import queue as _queue
import threading
import time
from ..agent import ToolAgent, _strip_role_leak
from ..context import build_sliding_window, needs_emergency_offload
from ..evaluate import run_evaluation
from ..models import SessionState
from ..session import InterviewSession
from .events import (
    EventQueue, status_event, delta_event, turn_end_event, error_event, done_event,
)


class InterviewTask:
    """一场活跃面试：后台线程 + 事件队列 + busy 锁 + 空闲超时。"""

    def __init__(self, user_id: str, session: InterviewSession, agent: ToolAgent,
                 queue: EventQueue, idle_timeout: float = 1800):
        self.user_id = user_id
        self.session = session
        self.agent = agent
        self.queue = queue
        self.idle_timeout = idle_timeout
        self.busy = False
        self.ended = False
        self.error: str | None = None
        self.report_url: str | None = None
        self.last_activity = time.time()
        self.shutdown = threading.Event()
        self._answers = _queue.Queue()
        self.thread = threading.Thread(
            target=self._run, daemon=True, name=f"interview-{session.session_id}"
        )

    def start(self) -> None:
        self.thread.start()

    def submit_answer(self, text: str) -> None:
        if self.busy:
            raise RuntimeError("面试官思考中，请稍候")
        self._answers.put(("answer", text))
        self.busy = True
        self.last_activity = time.time()

    def request_end(self) -> None:
        if self.busy:
            raise RuntimeError("面试官思考中，请稍候")
        self._answers.put(("end", None))
        self.busy = True
        self.last_activity = time.time()

    def _drain_repo_injection(self) -> None:
        """轮次边界注入后台仓库分析（不打断 assistant tool_calls 与结果的消息顺序）。"""
        text = self.session.drain_repo_injection()
        if text:
            self.queue.publish(status_event("github_ready"))

    def _run(self) -> None:
        try:
            self._drain_repo_injection()  # 审读极快完成的场景：开场前注入
            turn = self.agent.llm.chat(
                self.session.messages, tools=self.agent.registry.schemas()
            )
            opening = _strip_role_leak(turn.content or "你好，我是面试官，我们开始。")
            self.session.add_interviewer_message(opening)
            self.queue.publish(delta_event(opening))
            self.queue.publish(turn_end_event(self.session.question_count))
            self.session.begin_questions()
            while True:
                if self.shutdown.is_set():
                    return
                try:
                    item = self._answers.get(timeout=0.5)
                except _queue.Empty:
                    continue
                if item[0] == "end":
                    break
                self._drain_repo_injection()  # 用户回答期间审读完成的常规注入点
                self.session.add_candidate_message(item[1])
                cfg = self.session.config
                if needs_emergency_offload(
                    self.session.messages, cfg.max_context_chars, cfg.context_safety_ratio
                ):
                    self.session.messages = build_sliding_window(
                        self.session.messages,
                        cfg.keep_recent_messages,
                        "（上下文保护压缩）",
                    )
                    self.session.reapply_repo_analysis()
                self.queue.publish(status_event("thinking"))
                result = self.agent.run_turn(
                    on_delta=lambda t: self.queue.publish(delta_event(t))
                )
                self.busy = False
                self.last_activity = time.time()
                self.queue.publish(turn_end_event(self.session.question_count))
                if result.wrap_requested or self.session.state is SessionState.WRAPPING:
                    break
            self.queue.publish(status_event("evaluating"))
            self.session.to_evaluating()
            run_evaluation(self.session, self.agent.llm)
            self.session.to_done()
            self.report_url = f"/api/sessions/{self.session.session_id}/report"
            self.queue.publish(done_event(self.report_url))
        except Exception as e:  # noqa: BLE001 - 后台线程兜底
            self.error = str(e)
            self.queue.publish(error_event(f"面试异常：{e}"))
        finally:
            self.busy = False
            self.ended = True
