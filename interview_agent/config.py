"""配置加载：.env 密钥 + 环境变量默认值。"""
from __future__ import annotations
import os
from dotenv import load_dotenv


def load_config(env_path: str | None = None) -> dict:
    load_dotenv(env_path or ".env")
    return {
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "model": os.environ.get("INTERVIEW_MODEL", "deepseek-chat"),
        "min_questions": int(os.environ.get("INTERVIEW_MIN_QUESTIONS", "20")),
        "language": os.environ.get("INTERVIEW_LANG", "zh"),
        "answer_offload_threshold": int(
            os.environ.get("INTERVIEW_ANSWER_OFFLOAD_THRESHOLD", "100000")
        ),
        "context_safety_ratio": float(os.environ.get("INTERVIEW_CONTEXT_SAFETY_RATIO", "0.8")),
        "session_root": os.environ.get("INTERVIEW_ROOT", "interviews"),
    }


def require_api_key(env_path: str | None = None) -> str:
    key = load_config(env_path)["api_key"]
    if not key:
        raise KeyError("缺少 DEEPSEEK_API_KEY：请在 .env 中配置 DEEPSEEK_API_KEY，或设置同名环境变量")
    return key
