# tests/test_web_audio_service.py
import sys
import tempfile
import types
import pytest
from interview_agent.web.audio import AudioError, AudioService, DashScopeEngine


class FakeASRResult:
    status_code = 200
    def get_sentence(self):
        return [{"text": "你好，"}, {"text": "面试官。"}]


class FakeRecognition:
    last_kwargs = None
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeRecognition.last_kwargs = kwargs

    def call(self, path):
        return FakeASRResult()


class FakeSynthesizer:
    last_kwargs = None
    def __init__(self, **kwargs):
        FakeSynthesizer.last_kwargs = kwargs

    def call(self, text):
        return b"FAKEMP3"


def install_fake_dashscope(monkeypatch, asr_cls=None, synth_cls=None):
    dashscope = types.ModuleType("dashscope")
    audio_mod = types.ModuleType("dashscope.audio")
    asr_mod = types.ModuleType("dashscope.audio.asr")
    asr_mod.Recognition = asr_cls or FakeRecognition
    tts_mod = types.ModuleType("dashscope.audio.tts_v2")
    tts_mod.SpeechSynthesizer = synth_cls or FakeSynthesizer
    tts_mod.AudioFormat = types.SimpleNamespace(MP3_22050HZ_MONO_256KBPS="mp3")
    audio_mod.asr = asr_mod
    audio_mod.tts_v2 = tts_mod
    dashscope.audio = audio_mod
    for name, mod in [("dashscope", dashscope), ("dashscope.audio", audio_mod),
                      ("dashscope.audio.asr", asr_mod), ("dashscope.audio.tts_v2", tts_mod)]:
        monkeypatch.setitem(sys.modules, name, mod)


def test_engine_transcribe_joins_sentences(tmp_path, monkeypatch):
    install_fake_dashscope(monkeypatch)
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF")
    engine = DashScopeEngine("sk-test")
    assert engine.transcribe_file(p, "wav") == "你好，面试官。"
    assert FakeRecognition.last_kwargs["model"] == "paraformer-realtime-v2"
    assert FakeRecognition.last_kwargs["format"] == "wav"
    assert FakeRecognition.last_kwargs["language_hints"] == ["zh", "en"]
    assert FakeRecognition.last_kwargs["callback"] is None  # SDK 要求必填


def test_engine_asr_model_configurable(tmp_path, monkeypatch):
    install_fake_dashscope(monkeypatch)
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF")
    # qwen 系列：多语种 hint
    DashScopeEngine("sk-test", asr_model="qwen-audio-3.0-asr-flash-streaming").transcribe_file(p)
    assert FakeRecognition.last_kwargs["model"] == "qwen-audio-3.0-asr-flash-streaming"
    assert FakeRecognition.last_kwargs["language_hints"] == ["zh", "en"]
    # fun-asr 系列：仅支持 1 个语种
    DashScopeEngine("sk-test", asr_model="fun-asr-realtime").transcribe_file(p)
    assert FakeRecognition.last_kwargs["model"] == "fun-asr-realtime"
    assert FakeRecognition.last_kwargs["language_hints"] == ["zh"]


def test_engine_synthesize(tmp_path, monkeypatch):
    install_fake_dashscope(monkeypatch)
    engine = DashScopeEngine("sk-test", tts_model="my-tts-model")
    assert engine.synthesize("文本", "longshu_v2", 1.0) == b"FAKEMP3"
    assert FakeSynthesizer.last_kwargs["voice"] == "longshu_v2"
    assert FakeSynthesizer.last_kwargs["speech_rate"] == 1.0
    assert FakeSynthesizer.last_kwargs["model"] == "my-tts-model"


def test_service_transcribe_cleans_temp_file(tmp_path, monkeypatch):
    install_fake_dashscope(monkeypatch)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    svc = AudioService(DashScopeEngine("sk-test"))
    assert svc.transcribe(b"BYTES", "wav") == "你好，面试官。"
    assert list(tmp_path.glob("*.wav")) == []  # 临时文件已删除


def test_service_rejects_unknown_format():
    svc = AudioService(DashScopeEngine.__new__(DashScopeEngine))  # 不触 SDK
    with pytest.raises(AudioError, match="不支持的音频格式"):
        svc.transcribe(b"BYTES", "webm")


def test_service_wraps_unexpected_errors(tmp_path, monkeypatch):
    class BoomRecognition:
        def __init__(self, **kwargs): pass
        def call(self, path): raise RuntimeError("网络断了")
    install_fake_dashscope(monkeypatch, asr_cls=BoomRecognition)
    svc = AudioService(DashScopeEngine("sk-test"))
    with pytest.raises(AudioError, match="语音识别失败"):
        svc.transcribe(b"BYTES")


def test_from_config_provider_branches():
    # 无 key 无 provider：整体禁用（现状保持）
    assert AudioService.from_config({}) is None
    # 有 DASHSCOPE_API_KEY：自动走 dashscope
    svc = AudioService.from_config({"dashscope_api_key": "sk-x"})
    assert isinstance(svc.engine, DashScopeEngine)
    assert svc.engine.tts_model == "cosyvoice-v2"
    # 显式 dashscope 但缺 key：禁用而非报错
    assert AudioService.from_config({"audio_provider": "dashscope"}) is None
    svc2 = AudioService.from_config({
        "audio_provider": "dashscope", "dashscope_api_key": "sk-x",
        "tts_model": "cosyvoice-v1", "asr_model": "fun-asr-realtime",
    })
    assert svc2.engine.tts_model == "cosyvoice-v1"
    assert svc2.engine.asr_model == "fun-asr-realtime"
    # 本地引擎阶段 3 提供；未知 provider 直接报错
    with pytest.raises(ValueError, match="local"):
        AudioService.from_config({"audio_provider": "local"})
    with pytest.raises(ValueError, match="不支持的语音引擎"):
        AudioService.from_config({"audio_provider": "xxx"})


def test_load_config_audio_provider(tmp_path, monkeypatch):
    from interview_agent.config import load_config
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.delenv("INTERVIEW_AUDIO_PROVIDER", raising=False)
    assert load_config(str(env))["audio_provider"] == ""
    monkeypatch.setenv("INTERVIEW_AUDIO_PROVIDER", "local")
    assert load_config(str(env))["audio_provider"] == "local"


def test_load_config_tts_keys(tmp_path, monkeypatch):
    from interview_agent.config import load_config
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.setenv("INTERVIEW_TTS_MODEL", "cosyvoice-v1")
    monkeypatch.setenv("INTERVIEW_TTS_VOICE", "single-voice")
    monkeypatch.setenv("INTERVIEW_TTS_VOICE_SERIOUS", "serious-voice")
    cfg = load_config(str(env))
    assert cfg["tts_model"] == "cosyvoice-v1"
    assert cfg["tts_voice"] == "single-voice"
    assert cfg["tts_voice_by_style"] == {"serious": "serious-voice"}


def test_load_config_asr_model(tmp_path, monkeypatch):
    from interview_agent.config import load_config
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    monkeypatch.delenv("INTERVIEW_ASR_MODEL", raising=False)
    assert load_config(str(env))["asr_model"] == "paraformer-realtime-v2"
    monkeypatch.setenv("INTERVIEW_ASR_MODEL", "qwen-audio-3.0-asr-flash-streaming")
    assert load_config(str(env))["asr_model"] == "qwen-audio-3.0-asr-flash-streaming"
