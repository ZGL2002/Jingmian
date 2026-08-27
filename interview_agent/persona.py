# interview_agent/persona.py
"""面试官风格：动态背景主题、提示词人设、TTS 音色三合一的单一事实来源。"""
from __future__ import annotations
from dataclasses import dataclass

DEFAULT_KEY = "serious"


@dataclass(frozen=True)
class PersonaStyle:
    key: str
    label: str
    prompt_fragment: str
    tts_voice: str
    tts_speed: float


STYLES: dict[str, PersonaStyle] = {
    "serious": PersonaStyle(
        key="serious", label="严肃",
        prompt_fragment=(
            "你以严肃、专业的姿态面试：语气沉稳克制，不寒暄不打趣；"
            "回答不完整或含糊时立即追问细节，追问犀利、节奏紧凑；不轻易给正面反馈。"
        ),
        tts_voice="longshu_v2", tts_speed=1.0,
    ),
    "cold": PersonaStyle(
        key="cold", label="冷漠",
        prompt_fragment=(
            "你以冷漠、公事公办的姿态面试：回复尽量简短，只指出事实问题，"
            "不鼓励、不安慰、不寒暄；候选人答错时直接点破，不绕弯子。"
        ),
        tts_voice="longyingjing", tts_speed=0.95,
    ),
    "gentle": PersonaStyle(
        key="gentle", label="温和",
        prompt_fragment=(
            "你以温和、亲和的姿态面试：语气友好自然；候选人答对时给予简短肯定，"
            "答错时先肯定思路再指出问题，避免制造压迫感。"
        ),
        tts_voice="longyingling", tts_speed=0.95,
    ),
    "guide": PersonaStyle(
        key="guide", label="引导",
        prompt_fragment=(
            "你以循循善诱的姿态面试：候选人卡住时给出提示、帮其拆解问题，"
            "逐步引导其接近答案，鼓励其说出思考过程，而不是直接公布答案。"
        ),
        tts_voice="longxiaochun_v2", tts_speed=1.0,
    ),
}

_ORDER = ("serious", "cold", "gentle", "guide")


def get_style(key: str) -> PersonaStyle:
    return STYLES.get(key, STYLES[DEFAULT_KEY])


def resolve_voice(style_key: str, global_voice: str = "",
                  style_voices: dict[str, str] | None = None) -> str:
    """音色三级优先：按风格覆盖 > 全局覆盖 > 内置默认。

    单音色账号只需配置全局 INTERVIEW_TTS_VOICE，四种风格共用，
    风格差异仍由背景、人设与语速保留。
    """
    style_voices = style_voices or {}
    return style_voices.get(style_key) or global_voice or get_style(style_key).tts_voice


def list_styles() -> list[PersonaStyle]:
    return [STYLES[k] for k in _ORDER]


def style_keys() -> set[str]:
    return set(STYLES)
