import os
from interview_agent.config import load_config, require_api_key

def test_load_config_defaults(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = load_config(str(env))
    assert cfg["api_key"] == "sk-test"
    assert cfg["min_questions"] == 10
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


def test_load_config_feishu_keys(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("DEEPSEEK_API_KEY=x\n", encoding="utf-8")
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    cfg = load_config(str(env_file))
    assert cfg["feishu_app_id"] == ""
    assert cfg["feishu_app_secret"] == ""


def test_load_config_feishu_keys_from_env(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("DEEPSEEK_API_KEY=x\n", encoding="utf-8")
    monkeypatch.setenv("FEISHU_APP_ID", "cli_test123")
    monkeypatch.setenv("FEISHU_APP_SECRET", "sec_test")
    cfg = load_config(str(env_file))
    assert cfg["feishu_app_id"] == "cli_test123"
    assert cfg["feishu_app_secret"] == "sec_test"
