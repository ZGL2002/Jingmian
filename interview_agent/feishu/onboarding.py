"""对话式引导状态机：目标公司 → 岗位 → 简历收集。纯逻辑，不发消息。"""
from __future__ import annotations
from dataclasses import dataclass

ASK_COMPANY = "请回复目标公司名称（回复「跳过」不指定）"
ASK_POSITION = "请回复岗位名称，如「后端开发」（回复「跳过」不指定）"
ASK_RESUME = (
    "请粘贴简历内容：可分多条消息发送，最后单独发送一条「END」结束；"
    "回复「跳过」进入无简历模式。"
)


@dataclass
class OnboardingResult:
    company: str = ""
    position: str = ""
    resume_text: str = ""


class OnboardingFlow:
    """每用户一个实例；feed() 返回（待发送回复列表, 完成结果或 None）。"""

    COMPANY, POSITION, RESUME = "company", "position", "resume"

    def __init__(self):
        self.state = self.COMPANY
        self._result = OnboardingResult()
        self._resume_parts: list[str] = []

    @property
    def opening_question(self) -> str:
        return ASK_COMPANY

    def feed(self, text: str) -> tuple[list[str], OnboardingResult | None]:
        msg = text.strip()
        if self.state == self.COMPANY:
            if msg and msg != "跳过":
                self._result.company = msg
            self.state = self.POSITION
            return [ASK_POSITION], None
        if self.state == self.POSITION:
            if msg and msg != "跳过":
                self._result.position = msg
            self.state = self.RESUME
            return [ASK_RESUME], None
        # 简历收集：整条「跳过」→ 无简历；任一行为 END → 截到该行结束
        if msg == "跳过":
            return ["已跳过简历，正在开始面试…"], self._result
        lines = [l.strip() for l in text.split("\n")]
        if "END" in lines:
            idx = lines.index("END")
            self._resume_parts.append("\n".join(lines[:idx]).strip("\n"))
            self._result.resume_text = "\n".join(p for p in self._resume_parts if p)
            return ["简历已收到，正在开始面试…"], self._result
        self._resume_parts.append(text.strip("\n"))
        return [], None
