from pathlib import Path
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
        "a1", "a2", "结束",                    # 回答 + 主动结束
    ]
    report = run_cli(cfg, llm=llm, user_inputs=inputs)
    assert Path(report).name == "report.md"
    out = capsys.readouterr().out
    assert "你好，我是面试官" in out
    assert "第一个问题" in out
    assert "评估报告已生成" in out
