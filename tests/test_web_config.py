import pytest
from interview_agent.llm import create_llm, DeepSeekClient
from interview_agent.config import load_config, require_web_token


def test_create_llm_deepseek():
    assert isinstance(create_llm("sk-test"), DeepSeekClient)


def test_create_llm_unknown_provider():
    with pytest.raises(ValueError):
        create_llm("sk-test", provider="unknown")


def test_config_web_defaults(monkeypatch, tmp_path):
    for k in ("INTERVIEW_WEB_HOST", "INTERVIEW_WEB_PORT", "INTERVIEW_WEB_TOKEN",
              "INTERVIEW_SESSION_IDLE_TIMEOUT", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    cfg = load_config(str(env))
    assert cfg["web_host"] == "0.0.0.0"
    assert cfg["web_port"] == 8765
    assert cfg["web_token"] == ""
    assert cfg["session_idle_timeout"] == 1800


def test_config_web_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INTERVIEW_WEB_TOKEN", "t1")
    monkeypatch.setenv("INTERVIEW_WEB_PORT", "9000")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    cfg = load_config(str(env))
    assert cfg["web_token"] == "t1"
    assert cfg["web_port"] == 9000


def test_require_web_token():
    with pytest.raises(KeyError):
        require_web_token({"web_token": ""})
    assert require_web_token({"web_token": "x"}) == "x"
