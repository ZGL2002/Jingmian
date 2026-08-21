"""飞书机器人：事件规范化、四语境分发、单活跃会话约束、事件泵。"""
from __future__ import annotations
import json
import threading
from collections import deque
from .feishu_client import FeishuAPIError
from .onboarding import OnboardingFlow, OnboardingResult

THINKING_TEXT = "面试官思考中…"
EVALUATING_TEXT = "面试结束，正在生成评估报告…"
IDLE_HINT = "你好，我是模拟面试官。发送「开始面试」开始一场模拟面试，发送「帮助」查看完整用法。"
HELP_TEXT = (
    "【模拟面试官使用说明】\n"
    "1. 发送「开始面试」：按引导填写目标公司、岗位、简历（均可跳过）；\n"
    "2. 面试中直接发送消息即为回答；\n"
    "3. 发送「结束」随时结束并生成评估报告；\n"
    "4. 引导过程中发送「取消」可放弃配置。\n"
    "当前仅支持文字消息，语音面试将在后续版本支持。"
)
EMPTY_TURN_TEXT = "（本回合未产生内容，请再回答一次，或发送「结束」）"

START_CMDS = {"开始面试"}
END_CMDS = {"结束", "exit", "/end"}
HELP_CMDS = {"帮助", "help"}
CANCEL_CMDS = {"取消"}


class FeishuBot:
    def __init__(self, manager, client):
        self._manager = manager
        self._client = client
        self._flows: dict[str, OnboardingFlow] = {}
        self._active: dict[str, str] = {}  # open_id -> session_id
        self._pump_threads: dict[str, threading.Thread] = {}
        self._seen: deque[str] = deque(maxlen=1000)
        self._seen_set: set[str] = set()
        self._dispatch_lock = threading.Lock()

    # ---------- 入站：事件规范化（语音预留边界：未来在此加 audio→ASR） ----------

    def handle_event(self, event) -> None:
        """lark-oapi P2ImMessageReceiveV1 回调入口。"""
        try:
            event_id = event.header.event_id
            chat_type = event.event.message.chat_type
            msg_type = event.event.message.message_type
            open_id = event.event.sender.sender_id.open_id
            text = json.loads(event.event.message.content or "{}").get("text", "")
        except (AttributeError, TypeError, ValueError):
            return
        with self._dispatch_lock:
            if event_id in self._seen_set:
                return
            self._seen_set.add(event_id)
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(event_id)
        if chat_type != "p2p":
            return
        if msg_type != "text":
            self._send(open_id, "暂仅支持文字消息（语音面试将在后续版本支持）")
            return
        self.handle_message(open_id, text)

    # ---------- 分发 ----------

    def handle_message(self, open_id: str, text: str) -> None:
        cmd = text.strip()
        if cmd in HELP_CMDS:
            self._send(open_id, HELP_TEXT)
            return
        if cmd in END_CMDS:
            self._handle_end(open_id)
            return
        if cmd in START_CMDS:
            self._handle_start(open_id)
            return
        if cmd in CANCEL_CMDS and open_id in self._flows:
            self._flows.pop(open_id, None)
            self._send(open_id, "已取消本场配置。")
            return
        flow = self._flows.get(open_id)
        if flow is not None:
            replies, result = flow.feed(text)
            for r in replies:
                self._send(open_id, r)
            if result is not None:
                self._flows.pop(open_id, None)
                self._start_interview(open_id, result)
            return
        sid = self._active.get(open_id)
        if sid is not None:
            try:
                self._manager.submit_answer(open_id, sid, text)
            except (RuntimeError, KeyError) as e:
                self._send(open_id, str(e))
                if isinstance(e, KeyError):
                    self._active.pop(open_id, None)
            return
        self._send(open_id, IDLE_HINT)

    def _handle_start(self, open_id: str) -> None:
        if open_id in self._active:
            self._send(open_id, "已有进行中的面试，请先发送「结束」。")
            return
        flow = OnboardingFlow()
        self._flows[open_id] = flow
        self._send(open_id, flow.opening_question)

    def _handle_end(self, open_id: str) -> None:
        sid = self._active.get(open_id)
        if sid is not None:
            try:
                self._manager.end_session(open_id, sid)
            except (RuntimeError, KeyError) as e:
                self._send(open_id, str(e))
                if isinstance(e, KeyError):
                    self._active.pop(open_id, None)
            return
        if open_id in self._flows:
            self._flows.pop(open_id, None)
            self._send(open_id, "已取消本场配置。")
            return
        self._send(open_id, "当前没有进行中的面试。")

    def _start_interview(self, open_id: str, result: OnboardingResult) -> None:
        sid = self._manager.start_session(
            open_id,
            company=result.company,
            position=result.position,
            resume_text=result.resume_text or None,
        )
        self._active[open_id] = sid
        self._send(open_id, "面试已开始，直接回复消息作答；发送「结束」生成评估报告。")
        # 同步取出任务对象再传给泵：避免泵线程查询 manager 的竞态
        task = self._manager.get_task(open_id, sid)
        t = threading.Thread(
            target=self._pump, args=(open_id, sid, task), daemon=True,
            name=f"feishu-pump-{sid}",
        )
        self._pump_threads[sid] = t
        t.start()

    def _send(self, open_id: str, text: str) -> str | None:
        """发送失败（重试后）记日志跳过，不中断后续流程。"""
        try:
            return self._client.send_text(open_id, text)
        except FeishuAPIError as e:
            print(f"[feishu] 发送消息失败（已跳过）: {e}")
            return None

    # ---------- 出站：事件泵（语音预留边界：未来在此加文本→TTS 渲染） ----------

    POLL_INTERVAL = 1.0

    def _pump(self, open_id: str, session_id: str, task) -> None:
        if task is None:
            print(f"[feishu] 会话 {session_id} 无活跃任务，事件泵退出")
            return
        seq = 0
        placeholder_id: str | None = None
        buf: list[str] = []
        while True:
            items = task.queue.wait_for_events_after(seq, timeout=self.POLL_INTERVAL)
            for s, ev in items:
                seq = s
                kind = ev.get("type")
                if kind == "status" and ev.get("status") == "thinking":
                    if placeholder_id is None:
                        placeholder_id = self._send(open_id, THINKING_TEXT)
                elif kind == "delta":
                    if placeholder_id is None:
                        placeholder_id = self._send(open_id, THINKING_TEXT)
                    buf.append(ev.get("text", ""))
                elif kind == "turn_end":
                    content = "".join(buf) or EMPTY_TURN_TEXT
                    buf.clear()
                    if placeholder_id is not None:
                        if not self._client.patch_text(placeholder_id, content):
                            self._send(open_id, content)
                        placeholder_id = None
                    else:
                        self._send(open_id, content)
                elif kind == "status" and ev.get("status") == "evaluating":
                    self._send(open_id, EVALUATING_TEXT)
                elif kind == "status" and ev.get("status") == "done":
                    self._send_report(open_id, task)
                    self._cleanup(open_id, session_id)
                    return
                elif kind == "error":
                    self._send(open_id, f"面试出错了：{ev.get('message', '')}")
                    self._cleanup(open_id, session_id)
                    return
            if not items and task.ended:
                self._send(open_id, "本场面试已结束（空闲超时或异常）。发送「开始面试」可再来一场。")
                self._cleanup(open_id, session_id)
                return

    def _send_report(self, open_id: str, task) -> None:
        p = task.session.session_dir / "report.md"
        if p.is_file():
            self._client.send_markdown_card(
                open_id, "面试评估报告", p.read_text(encoding="utf-8")
            )
        else:
            self._send(open_id, "评估已完成但报告文件缺失，请联系管理员在服务器会话目录查看。")

    def _cleanup(self, open_id: str, session_id: str) -> None:
        if self._active.get(open_id) == session_id:
            del self._active[open_id]
        self._pump_threads.pop(session_id, None)
