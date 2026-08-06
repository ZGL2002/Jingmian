"""终端入口：简历输入、逐轮对话、结束、展示报告。"""
from __future__ import annotations
from pathlib import Path
from .config import load_config, require_api_key
from .models import SessionConfig, SessionState
from .resume import extract_text, parse_resume
from .session import InterviewSession
from .agent import ToolAgent
from .evaluate import run_evaluation
from .llm import DeepSeekClient, LLMError
from .tools import default_registry
from .tools.base import ToolContext
from .security import PathPolicy
from .context import build_sliding_window, needs_emergency_offload
from .storage import atomic_write

END_COMMANDS = {"结束", "exit", "/end"}
PASTE_END = "END"
RESUME_SUFFIXES = {".txt", ".md", ".pdf"}


def _default_user_id(cfg: dict) -> str:
    if cfg.get("user_id"):
        return cfg["user_id"]
    try:
        import getpass
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "local"


def _finish_resume(lines: list[str]) -> str | None:
    text = "\n".join(lines).strip()
    if not text:
        return None
    p = Path(text)
    if len(lines) == 1 and p.suffix.lower() in RESUME_SUFFIXES and p.is_file():
        return text
    return text


def _read_resume(user_inputs: list[str] | None) -> tuple[str | None, list[str] | None]:
    if user_inputs is None:
        print("请粘贴简历文本（完成后输入一行 END），或直接输入文件路径（.txt/.md/.pdf）：")
        lines = []
        while True:
            line = input()
            if line.strip() == PASTE_END:
                break
            lines.append(line)
        return _finish_resume(lines), None
    consumed: list[str] = []
    remaining: list[str] = []
    for i, line in enumerate(user_inputs):
        if line.strip() == PASTE_END:
            remaining = user_inputs[i + 1:]
            break
        consumed.append(line)
    return _finish_resume(consumed), remaining or None


def run_cli(cfg: dict, llm=None, user_inputs: list[str] | None = None) -> str:
    raw, remaining = _read_resume(user_inputs)
    try:
        resume = parse_resume(extract_text(raw)) if raw else None
    except ValueError as e:
        raise SystemExit(f"错误：{e}") from None
    config = SessionConfig(
        user_id=_default_user_id(cfg),
        session_root=Path(cfg["session_root"]),
        min_questions=int(cfg["min_questions"]),
        language=cfg.get("language", "zh"),
        model=cfg.get("model", "deepseek-chat"),
        answer_offload_threshold=int(cfg.get("answer_offload_threshold", 100_000)),
        context_safety_ratio=float(cfg.get("context_safety_ratio", 0.8)),
    )
    session = InterviewSession(config, resume)
    session.start()
    registry = default_registry()
    tool_ctx = ToolContext(
        user_id=config.user_id,
        session_dir=session.session_dir,
        policy=PathPolicy([session.session_dir]),
        transcript_path=session.transcript_path,
        wrap_allowed=session.can_auto_wrap,
    )
    if llm is None:
        llm = DeepSeekClient(require_api_key(), model=config.model)
    agent = ToolAgent(llm, registry, session, tool_ctx)

    opening = llm.chat(session.messages, tools=registry.schemas())
    opening_text = opening.content or "你好，我是面试官，我们开始。"
    session.add_interviewer_message(opening_text)  # OPENING 状态不计题数
    print(opening_text)
    session.begin_questions()

    lines = remaining
    index = 0
    while True:
        try:
            if lines is None:
                text = input("你> ")
            else:
                if index >= len(lines):
                    break
                text = lines[index]
                index += 1
        except KeyboardInterrupt:
            print("\n已中断面试，正在基于已记录内容生成评估报告…")
            session.to_wrapping()
            break
        if text.strip() in END_COMMANDS:
            session.to_wrapping()
            break
        session.add_candidate_message(text)
        if needs_emergency_offload(session.messages, config.max_context_chars, config.context_safety_ratio):
            atomic_write(session.session_dir / "summary.md", "上下文保护：滑动窗口已压缩")
            session.messages = build_sliding_window(session.messages, config.keep_recent_messages, "（上下文保护压缩）")
        result = agent.run_turn()
        print(result.content)
        if result.wrap_requested or session.state is SessionState.WRAPPING:
            break

    session.to_evaluating()
    report_path = run_evaluation(session, llm)
    session.to_done()
    print(f"\n面试结束，评估报告已生成：{report_path}")
    return str(report_path)


def main() -> None:
    cfg = load_config()
    try:
        require_api_key()
    except KeyError as e:
        print(e)
        raise SystemExit(1) from None
    try:
        run_cli(cfg)
    except LLMError as e:
        print(f"错误：{e}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
