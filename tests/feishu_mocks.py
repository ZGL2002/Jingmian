"""飞书渠道测试共享替身：内存客户端 / 假 Manager / 假任务 / 脚本 LLM。"""
import time
from types import SimpleNamespace
from interview_agent.llm import AssistantTurn, StreamEnd
from interview_agent.web.events import EventQueue


class MockFeishuClient:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []        # (open_id, text)
        self.patched: list[tuple[str, str]] = []     # (message_id, text)
        self.cards: list[tuple[str, str, str]] = []  # (open_id, title, markdown)
        self.patch_ok = True
        self._n = 0

    def send_text(self, open_id, text):
        self._n += 1
        self.sent.append((open_id, text))
        return f"om_mock_{self._n}"

    def patch_text(self, message_id, text):
        if not self.patch_ok:
            return False
        self.patched.append((message_id, text))
        return True

    def send_markdown_card(self, open_id, title, markdown_text):
        self.cards.append((open_id, title, markdown_text))

    def texts_to(self, open_id):
        return [t for o, t in self.sent if o == open_id]


class FakeManager:
    """记录调用；get_task 返回注入的假任务。"""

    def __init__(self):
        self.started: list[dict] = []
        self.answers: list[tuple[str, str, str]] = []
        self.ends: list[tuple[str, str]] = []
        self.tasks: dict[tuple[str, str], object] = {}
        self.submit_error: Exception | None = None

    def start_session(self, user_id, *, company="", position="",
                      resume_text=None, jd_text="", experiences=None):
        self.started.append({"user_id": user_id, "company": company,
                             "position": position, "resume_text": resume_text})
        return f"sess{len(self.started)}"

    def get_task(self, user_id, session_id):
        return self.tasks.get((user_id, session_id))

    def submit_answer(self, user_id, session_id, text):
        if self.submit_error is not None:
            raise self.submit_error
        self.answers.append((user_id, session_id, text))

    def end_session(self, user_id, session_id):
        self.ends.append((user_id, session_id))


class FakeTask:
    def __init__(self, session_dir):
        self.queue = EventQueue()
        self.session = SimpleNamespace(session_dir=session_dir)  # 与真实 InterviewTask 同形
        self.ended = False


class ScriptLLM:
    """按脚本出牌的 LLM：chat 与 chat_stream 共用同一队列。"""

    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False
