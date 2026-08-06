import os
from interview_agent.config import load_config, require_api_key

def test_load_config_defaults(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(str(env))
    assert cfg["api_key"] == "sk-test"
    assert cfg["min_questions"] == 20
    assert cfg["model"] == "deepseek-chat"
    assert cfg["language"] == "zh"
    assert cfg["answer_offload_threshold"] == 100_000
    assert cfg["context_safety_ratio"] == 0.8
    assert cfg["session_root"] == "interviews"

def test_require_api_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        require_api_key(str(tmp_path / "empty.env"))
        raise AssertionError("应当抛出 KeyError")
    except KeyError:
        pass
