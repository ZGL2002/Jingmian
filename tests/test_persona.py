# tests/test_persona.py
from interview_agent.persona import get_style, list_styles, resolve_voice, style_keys


def test_four_styles_in_order():
    assert [s.key for s in list_styles()] == ["serious", "cold", "gentle", "guide"]
    assert [s.label for s in list_styles()] == ["严肃", "冷漠", "温和", "引导"]


def test_unknown_key_falls_back_to_serious():
    assert get_style("nope").key == "serious"
    assert get_style("").key == "serious"


def test_style_fields_complete():
    for s in list_styles():
        assert s.prompt_fragment and s.tts_voice and 0.5 <= s.tts_speed <= 2.0


def test_style_keys_matches_list():
    assert style_keys() == {s.key for s in list_styles()}


def test_resolve_voice_priority():
    assert resolve_voice("cold") == "longyingjing"                    # 内置默认
    assert resolve_voice("cold", "my-only-voice") == "my-only-voice"  # 全局覆盖
    assert resolve_voice("cold", "my-only-voice", {"cold": "special"}) == "special"  # 按风格覆盖最高
    assert resolve_voice("serious", "my-only-voice") == "my-only-voice"  # 全局对任意风格生效
    assert resolve_voice("guide", "", {"serious": "x"}) == "longxiaochun_v2"  # 无关覆盖不生效
