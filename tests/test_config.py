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


def test_load_config_provider_default(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("INTERVIEW_PROVIDER", raising=False)
    cfg = load_config(str(env_file))
    assert cfg["provider"] == "deepseek"


def test_load_config_dashscope_key(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("INTERVIEW_PROVIDER=dashscope\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-ali-test")
    cfg = load_config(str(env_file))
    assert cfg["provider"] == "dashscope"
    assert cfg["api_key"] == "sk-ali-test"


def test_require_api_key_dashscope_missing_names_right_env(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("INTERVIEW_PROVIDER=dashscope\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    try:
        require_api_key(str(env_file))
        raise AssertionError("应当抛出 KeyError")
    except KeyError as e:
        assert "DASHSCOPE_API_KEY" in str(e)


def test_load_config_zhipu_key(tmp_path, monkeypatch):
    # setenv 而非只写 env 文件：load_dotenv 非 override 模式下，
    # 之前测试遗留的 INTERVIEW_PROVIDER 会盖住文件内容
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("INTERVIEW_PROVIDER", "zhipu")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "sk-zp-test")
    cfg = load_config(str(env_file))
    assert cfg["provider"] == "zhipu"
    assert cfg["api_key"] == "sk-zp-test"


def test_require_api_key_zhipu_missing_names_right_env(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("INTERVIEW_PROVIDER", "zhipu")
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    try:
        require_api_key(str(env_file))
        raise AssertionError("应当抛出 KeyError")
    except KeyError as e:
        assert "ZHIPU_API_KEY" in str(e)


def test_load_config_default_model_follows_provider(tmp_path, monkeypatch):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.delenv("INTERVIEW_MODEL", raising=False)
    monkeypatch.setenv("INTERVIEW_PROVIDER", "zhipu")
    assert load_config(str(env_file))["model"] == "glm-4.7-flash"
    monkeypatch.setenv("INTERVIEW_PROVIDER", "dashscope")
    assert load_config(str(env_file))["model"] == "qwen-plus"
    monkeypatch.setenv("INTERVIEW_MODEL", "glm-4.7-flash")
    assert load_config(str(env_file))["model"] == "glm-4.7-flash"


def test_load_config_github_defaults(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for k in ("INTERVIEW_GITHUB_ANALYSIS", "GITHUB_TOKEN", "INTERVIEW_GITHUB_MAX_REPOS"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(str(env))
    assert cfg["github_analysis_enabled"] is True
    assert cfg["github_token"] == ""
    assert cfg["github_max_repos"] == 3


def test_load_config_github_env_overrides(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INTERVIEW_GITHUB_ANALYSIS", "0")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("INTERVIEW_GITHUB_MAX_REPOS", "5")
    cfg = load_config(str(env))
    assert cfg["github_analysis_enabled"] is False
    assert cfg["github_token"] == "ghp_x"
    assert cfg["github_max_repos"] == 5
