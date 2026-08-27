# tests/test_web_audio_stream.py
import sys
import types
import pytest
from interview_agent.web.audio import AudioService, DashScopeEngine, StreamRecognizer


class FakeStreamResult:
    def __init__(self, text, end):
        self._sentence = {"text": text, "end": end}

    def get_sentence(self):
        return self._sentence


class FakeRecognitionResult:
    @staticmethod
    def is_sentence_end(sentence):
        return sentence.get("end") is True


class FakeStreamRecognition:
    """流式识别 fake：send_audio_frame 直接同步回调 final，记录构造参数。"""
    last = None
    stopped = False

    def __init__(self, callback=None, **kwargs):
        self.callback = callback
        self.kwargs = kwargs
        FakeStreamRecognition.last = self

    def start(self):
        self.started = True

    def send_audio_frame(self, frame):
        text = frame.decode("utf-8", "replace")
        self.callback.on_event(FakeStreamResult("听" + text, False))   # 先 partial
        self.callback.on_event(FakeStreamResult("你好" + text, True))  # 再 final

    def stop(self):
        FakeStreamRecognition.stopped = True


class FakeResultCallback:
    def __init__(self):
        self.chunks = []

    def on_data(self, data):
        self.chunks.append(bytes(data))


class FakeStreamingSynth:
    last = None
    callback_obj = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        FakeStreamingSynth.last = self

    def streaming_call(self, text):
        self.text = text
        cb = FakeStreamingSynth.callback_obj
        cb.on_data(b"\x01\x02")
        cb.on_data(b"\x03")

    def streaming_complete(self):
        pass


def install_fake_stream_dashscope(monkeypatch):
    dashscope = types.ModuleType("dashscope")
    audio_mod = types.ModuleType("dashscope.audio")
    asr_mod = types.ModuleType("dashscope.audio.asr")
    asr_mod.Recognition = FakeStreamRecognition
    asr_mod.RecognitionCallback = object
    asr_mod.RecognitionResult = FakeRecognitionResult
    tts_mod = types.ModuleType("dashscope.audio.tts_v2")
    tts_mod.SpeechSynthesizer = FakeStreamingSynth
    tts_mod.ResultCallback = FakeResultCallback
    tts_mod.AudioFormat = types.SimpleNamespace(
        MP3_22050HZ_MONO_256KBPS="mp3", PCM_22050HZ_MONO_16BIT="pcm16",
    )
    # 让真实类成为 callback 基类，捕获实例
    class Cap(FakeResultCallback):
        def __init__(self):
            super().__init__()
            FakeStreamingSynth.callback_obj = self
    tts_mod.ResultCallback = Cap
    audio_mod.asr = asr_mod
    audio_mod.tts_v2 = tts_mod
    dashscope.audio = audio_mod
    for name, mod in [("dashscope", dashscope), ("dashscope.audio", audio_mod),
                      ("dashscope.audio.asr", asr_mod), ("dashscope.audio.tts_v2", tts_mod)]:
        monkeypatch.setitem(sys.modules, name, mod)


def test_stream_recognizer_params_and_events(monkeypatch):
    install_fake_stream_dashscope(monkeypatch)
    events = []
    rec = StreamRecognizer(DashScopeEngine("sk", asr_model="qwen-audio-3.0-asr-flash-streaming"),
                           lambda ev: events.append(ev))
    rec.start()
    rec.feed(b"x")
    rec.stop()
    kw = FakeStreamRecognition.last.kwargs
    assert kw["format"] == "pcm"
    assert kw["sample_rate"] == 16000
    assert kw["max_sentence_silence"] == 800
    assert kw["language_hints"] == ["zh", "en"]
    assert FakeStreamRecognition.last.started
    assert FakeStreamRecognition.stopped
    assert events == [
        {"type": "partial", "text": "听x"},
        {"type": "final", "text": "你好x"},
    ]


def test_stream_recognizer_fun_asr_single_hint(monkeypatch):
    install_fake_stream_dashscope(monkeypatch)
    rec = StreamRecognizer(DashScopeEngine("sk", asr_model="fun-asr-realtime"), lambda ev: None)
    rec.start()
    assert FakeStreamRecognition.last.kwargs["language_hints"] == ["zh"]


def test_engine_synthesize_stream(monkeypatch):
    install_fake_stream_dashscope(monkeypatch)
    engine = DashScopeEngine("sk", tts_model="qwen-audio-3.0-tts-flash")
    chunks = []
    engine.synthesize_stream("一句话", "longanfengyue", 1.0, lambda c: chunks.append(c))
    assert chunks == [b"\x01\x02", b"\x03"]
    kw = FakeStreamingSynth.last.kwargs
    assert kw["model"] == "qwen-audio-3.0-tts-flash"
    assert kw["voice"] == "longanfengyue"
    assert kw["speech_rate"] == 1.0
    assert kw["format"] == "pcm16"
    assert FakeStreamingSynth.last.text == "一句话"


def test_stream_recognizer_requires_engine():
    with pytest.raises(TypeError):
        StreamRecognizer()  # engine/on_event 必填
