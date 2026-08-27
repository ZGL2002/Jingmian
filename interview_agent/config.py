"""配置加载：.env 密钥 + 环境变量默认值。"""
from __future__ import annotations
import os
from dotenv import load_dotenv

# 各 provider 对应的密钥环境变量名
PROVIDER_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "dashscope": "DASHSCOPE_API_KEY",
}


def load_config(env_path: str | None = None) -> dict:
    load_dotenv(env_path or ".env")
    provider = os.environ.get("INTERVIEW_PROVIDER", "deepseek")
    key_env = PROVIDER_KEY_ENV.get(provider, "DEEPSEEK_API_KEY")
    return {
        "provider": provider,
        "api_key": os.environ.get(key_env, ""),
        "model": os.environ.get("INTERVIEW_MODEL", "deepseek-chat"),
        "min_questions": int(os.environ.get("INTERVIEW_MIN_QUESTIONS", "10")),
        "language": os.environ.get("INTERVIEW_LANG", "zh"),
        "answer_offload_threshold": int(
            os.environ.get("INTERVIEW_ANSWER_OFFLOAD_THRESHOLD", "100000")
        ),
        "context_safety_ratio": float(os.environ.get("INTERVIEW_CONTEXT_SAFETY_RATIO", "0.8")),
        "session_root": os.environ.get("INTERVIEW_ROOT", "interviews"),
        "feishu_app_id": os.environ.get("FEISHU_APP_ID", ""),
        "feishu_app_secret": os.environ.get("FEISHU_APP_SECRET", ""),
        "web_host": os.environ.get("INTERVIEW_WEB_HOST", "0.0.0.0"),
        "web_port": int(os.environ.get("INTERVIEW_WEB_PORT", "8765")),
        "web_token": os.environ.get("INTERVIEW_WEB_TOKEN", ""),
        "session_idle_timeout": int(os.environ.get("INTERVIEW_SESSION_IDLE_TIMEOUT", "1800")),
        "dashscope_api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "asr_model": os.environ.get("INTERVIEW_ASR_MODEL", "paraformer-realtime-v2"),
        "tts_model": os.environ.get("INTERVIEW_TTS_MODEL", "cosyvoice-v2"),
        "tts_voice": os.environ.get("INTERVIEW_TTS_VOICE", ""),
        # INTERVIEW_TTS_VOICE_<STYLE>（如 _SERIOUS）按风格覆盖；全局键 INTERVIEW_TTS_VOICE
        # 本身（无后缀）不匹配 startswith 后的下划线要求，不会进入此 dict
        "tts_voice_by_style": {
            k[len("INTERVIEW_TTS_VOICE_"):].lower(): v
            for k, v in os.environ.items()
            if k.startswith("INTERVIEW_TTS_VOICE_") and v
        },
    }


def require_api_key(env_path: str | None = None) -> str:
    cfg = load_config(env_path)
    key = cfg["api_key"]
    if not key:
        key_env = PROVIDER_KEY_ENV.get(cfg["provider"], "DEEPSEEK_API_KEY")
        raise KeyError(
            f"缺少 {key_env}：请在 .env 中配置 {key_env}"
            f"（当前 INTERVIEW_PROVIDER={cfg['provider']}），或设置同名环境变量"
        )
    return key


def require_web_token(cfg: dict) -> str:
    token = cfg.get("web_token", "")
    if not token:
        raise KeyError("缺少 INTERVIEW_WEB_TOKEN：请在 .env 中配置 Web 访问口令")
    return token
