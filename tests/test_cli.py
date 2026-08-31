from pathlib import Path
import json
import pytest
from interview_agent.cli import run_cli
from interview_agent.llm import AssistantTurn

class CliLLM:
    def __init__(self, script):
        self.script = list(script)
    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

def test_run_cli_full_flow(tmp_path, monkeypatch, capsys):
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path),
    }
    llm = CliLLM([
        AssistantTurn(content="你好，我是面试官，我们开始。"),
        AssistantTurn(content="第一个问题：介绍你的订单系统？"),
        AssistantTurn(content="第二个问题：Redis 为什么快？"),
        AssistantTurn(content="## 技术准确性\n9 分。"),
    ])
    inputs = [
        "熟悉 Python，做过订单系统", "END",  # 简历粘贴
        "a1", "", "a2", "", "结束",            # 回答（空行提交）+ 主动结束
    ]
    report = run_cli(cfg, llm=llm, user_inputs=inputs)
    assert Path(report).name == "report.md"
    out = capsys.readouterr().out
    assert "你好，我是面试官" in out
    assert "第一个问题" in out
    assert out.count("第一个问题") == 1
    assert "今天的面试到这里正式结束" in out
    assert "评估报告生成中，请稍候" in out
    assert "评估报告已生成" in out

def test_multiline_answer_is_single_message(tmp_path, capsys):
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path),
    }
    llm = CliLLM([
        AssistantTurn(content="你好，我们开始。"),
        AssistantTurn(content="第一个问题：介绍一下你的项目？"),
        AssistantTurn(content="## 技术准确性\n8 分。"),
    ])
    inputs = [
        "熟悉 Python", "END",
        "第一段回答", "第二段回答", "",   # 多行回答，空行提交
        "结束",
    ]
    report = run_cli(cfg, llm=llm, user_inputs=inputs)
    sdir = Path(report).parent
    entries = [json.loads(line) for line in (sdir / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    cands = [e for e in entries if e["role"] == "candidate"]
    assert len(cands) == 1
    assert "第一段回答" in cands[0]["content"]
    assert "第二段回答" in cands[0]["content"]

def test_empty_answer_skipped(tmp_path, capsys):
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path),
    }
    llm = CliLLM([
        AssistantTurn(content="你好，我们开始。"),
        AssistantTurn(content="第一个问题？"),
        AssistantTurn(content="## 技术准确性\n8 分。"),
    ])
    inputs = [
        "熟悉 Python", "END",
        "",            # 直接回车：空提交，应被跳过
        "真实回答", "",
        "结束",
    ]
    report = run_cli(cfg, llm=llm, user_inputs=inputs)
    sdir = Path(report).parent
    entries = [json.loads(line) for line in (sdir / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    cands = [e for e in entries if e["role"] == "candidate"]
    assert len(cands) == 1
    assert cands[0]["content"] == "真实回答"

def test_eof_ends_gracefully(tmp_path, monkeypatch, capsys):
    import builtins
    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(EOFError()))
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path),
    }
    llm = CliLLM([
        AssistantTurn(content="你好，我们开始。"),
        AssistantTurn(content="## 技术准确性\n8 分。"),
    ])
    report = run_cli(cfg, llm=llm, user_inputs=None)
    assert Path(report).name == "report.md"
    out = capsys.readouterr().out
    assert "今天的面试到这里正式结束" in out
    assert "评估报告生成中，请稍候" in out

def test_run_cli_scanned_pdf_raises_system_exit(tmp_path):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    pdf = tmp_path / "scan.pdf"
    with pdf.open("wb") as f:
        w.write(f)
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path / "interviews"),
    }
    with pytest.raises(SystemExit):
        run_cli(cfg, llm=CliLLM([]), user_inputs=[str(pdf), "END"])


def _canned_github(monkeypatch):
    """预置 GitHub API 响应：alice/shop 元信息 + 文件树 + README。"""
    import base64
    import io
    import json as _json

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        if url.endswith("/repos/alice/shop"):
            payload = {"default_branch": "main", "description": "商城", "language": "Python", "stargazers_count": 1}
        elif "git/trees" in url:
            payload = {"tree": [{"path": "README.md", "type": "blob", "size": 12}]}
        elif "contents/README.md" in url:
            payload = {"content": base64.b64encode("# shop\n".encode()).decode(), "encoding": "base64"}
        else:
            raise AssertionError(f"意外请求: {url}")
        return FakeResponse(_json.dumps(payload).encode())

    monkeypatch.setattr("interview_agent.github.urlopen", fake_urlopen)


def test_run_cli_parallel_repo_analysis_injection(tmp_path, monkeypatch, capsys):
    """开面试不等待：审读在回答期间后台完成，下一轮注入面试官资料。"""
    from interview_agent.repo_agent import start_repo_analysis

    _canned_github(monkeypatch)
    monkeypatch.setattr(
        "interview_agent.cli.start_repo_analysis",
        lambda s, l, on_progress=None: start_repo_analysis(s, l, on_progress=on_progress, synchronous=True),
    )
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path),
        "github_analysis_enabled": True, "github_token": "tk", "github_max_repos": 1,
    }
    llm = CliLLM([
        AssistantTurn(content="## 项目结构概述\n单模块商城"),   # 审读子 agent
        AssistantTurn(content="你好，我是面试官"),               # 开场
        AssistantTurn(content="第一个问题：整体架构？"),         # 审读完成前的架构题
        AssistantTurn(content="## 技术准确性\n8 分。"),          # 评估
    ])
    inputs = ["项目：商城 https://github.com/alice/shop", "END", "a1", "", "结束"]
    report = run_cli(cfg, llm=llm, user_inputs=inputs)
    sdir = Path(report).parent
    prompt = (sdir / "prompt.md").read_text(encoding="utf-8")
    assert "后台" in prompt and "alice/shop" in prompt       # 开场引导段
    assert "GitHub 仓库代码分析" not in prompt               # 分析不进首条提示词
    transcript = [json.loads(line) for line in (sdir / "transcript.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("role") == "note" and "GitHub" in e["content"] for e in transcript)
    assert (sdir / "repos" / "alice__shop" / "analysis.md").exists()
    out = capsys.readouterr().out
    assert "已收到" in out


def test_run_cli_github_disabled_keeps_prompt_clean(tmp_path, monkeypatch, capsys):
    def boom(req, timeout=None):
        raise AssertionError("不应触网")

    monkeypatch.setattr("interview_agent.github.urlopen", boom)
    cfg = {
        "api_key": "sk-test", "model": "deepseek-chat",
        "min_questions": 3, "language": "zh",
        "answer_offload_threshold": 100_000, "context_safety_ratio": 0.8,
        "session_root": str(tmp_path),
        "github_analysis_enabled": False,
    }
    llm = CliLLM([
        AssistantTurn(content="你好，我是面试官"),
        AssistantTurn(content="第一个问题：介绍项目？"),
        AssistantTurn(content="## 技术准确性\n8 分。"),
    ])
    inputs = ["项目：商城 https://github.com/alice/shop", "END", "a1", "", "结束"]
    report = run_cli(cfg, llm=llm, user_inputs=inputs)
    prompt = (Path(report).parent / "prompt.md").read_text(encoding="utf-8")
    assert "GitHub 仓库代码分析" not in prompt
    assert not (Path(report).parent / "repos").exists()
    assert "正在分析" not in capsys.readouterr().out
