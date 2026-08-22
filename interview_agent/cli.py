"""终端入口：简历输入、逐轮对话、结束、展示报告。"""
from __future__ import annotations
from pathlib import Path
from .config import load_config, require_api_key
from .models import SessionConfig, SessionState
from .resume import extract_text, parse_resume
from .session import InterviewSession
from .agent import ToolAgent, _strip_role_leak
from .evaluate import run_evaluation
from .llm import LLMError, create_llm
from .tools import default_registry
from .tools.base import ToolContext
from .security import PathPolicy
from .context import build_sliding_window, needs_emergency_offload
from .storage import atomic_write

END_COMMANDS = {"结束", "exit", "/end"}
PASTE_END = "END"
RESUME_SUFFIXES = {".txt", ".md", ".pdf"}
END_SENTINEL = "__END__"


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
            try:
                line = input()
            except EOFError:
                break
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


def _read_answer(lines: list[str] | None, index: int, interactive: bool) -> tuple[str | None, int]:
    """读取一条回答：连续非空行合并为一段，空行提交；END 命令仅在回答开头生效；EOF/耗尽返回 None。"""
    parts: list[str] = []
    while True:
        if interactive:
            try:
                line = input("你> " if not parts else "…> ")
            except EOFError:
                return ("\n".join(parts) or None), index
        else:
            if index >= len(lines or []):
                return ("\n".join(parts) or None), index
            line = lines[index]
            index += 1
        if not parts and line.strip() in END_COMMANDS:
            return END_SENTINEL, index
        if not line.strip():
            return ("\n".join(parts) or ""), index
        parts.append(line)


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
        llm = create_llm(
            require_api_key(), model=config.model,
            provider=cfg.get("provider", "deepseek"),
        )
    agent = ToolAgent(llm, registry, session, tool_ctx)

    opening = llm.chat(session.messages, tools=registry.schemas())
    opening_text = _strip_role_leak(opening.content or "你好，我是面试官，我们开始。")
    session.add_interviewer_message(opening_text)  # OPENING 状态不计题数
    print(opening_text)
    session.begin_questions()
    print("提示：回答支持多行，粘贴后按一个空行回车提交；直接回车不提交；输入 结束/exit//end 结束面试。")

    lines = remaining
    index = 0
    while True:
        try:
            text, index = _read_answer(lines, index, lines is None)
        except KeyboardInterrupt:
            print("\n已中断面试，正在基于已记录内容生成评估报告…")
            session.to_wrapping()
            break
        if text is None or text == END_SENTINEL:
            session.to_wrapping()
            break
        if not text.strip():
            continue
        session.add_candidate_message(text)
        if needs_emergency_offload(session.messages, config.max_context_chars, config.context_safety_ratio):
            atomic_write(session.session_dir / "summary.md", "上下文保护：滑动窗口已压缩")
            session.messages = build_sliding_window(session.messages, config.keep_recent_messages, "（上下文保护压缩）")
        result = agent.run_turn()
        print(result.content)
        if result.wrap_requested or session.state is SessionState.WRAPPING:
            break

    print("\n好的，今天的面试到这里正式结束。感谢你的参与！")
    print("评估报告生成中，请稍候…")
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
