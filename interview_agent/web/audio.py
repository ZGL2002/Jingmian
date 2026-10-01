# interview_agent/web/audio.py
"""语音服务：引擎抽象 + DashScope 实现（Paraformer 识别 + CosyVoice 合成）。

dashscope SDK 在方法体内延迟 import：未安装/未使用语音的渠道不受影响。
引擎接口（DashScopeEngine / LocalEngine 一致）：
  transcribe_file / synthesize / synthesize_stream / create_stream_recognizer
"""
from __future__ import annotations
import tempfile
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

ASR_FORMATS = {"wav", "mp3", "opus", "speex", "aac", "amr"}
STREAM_SILENCE_MS = 800  # 流式识别句尾静音判定（毫秒）


class AudioError(Exception):
    """语音识别/合成失败，上层转 HTTP 错误。"""


class _DashScopeStreamSession:
    """DashScope Recognition 流式会话：16k PCM 帧 → 事件回调。

    on_event 在 SDK 回调线程被调用，收到 dict：
      {"type": "partial"|"final"|"error", "text"/"message": str}
    """

    def __init__(self, asr_model: str, on_event: Callable[[dict], None]):
        self._asr_model = asr_model
        self._on_event = on_event
        self._rec = None

    def start(self) -> None:
        from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
        if self._asr_model.startswith("fun-asr"):
            hints = ["zh"]
        else:
            hints = ["zh", "en"]
        on_event = self._on_event

        class _Cb(RecognitionCallback):
            def on_event(self, result):
                sentence = result.get_sentence()
                if not isinstance(sentence, dict):
                    return
                if RecognitionResult.is_sentence_end(sentence):
                    on_event({"type": "final", "text": sentence.get("text", "")})
                else:
                    on_event({"type": "partial", "text": sentence.get("text", "")})

            def on_error(self, result):
                on_event({"type": "error", "message": str(getattr(result, "message", "识别错误"))})

        self._rec = Recognition(
            model=self._asr_model, callback=_Cb(), format="pcm",
            sample_rate=16000, max_sentence_silence=STREAM_SILENCE_MS,
            disfluency_removal_enabled=True,  # 服务端去语气词（嗯/呃等），减少"嗯。"被当回答
            language_hints=hints,
        )
        self._rec.start()

    def feed(self, pcm: bytes) -> None:
        if self._rec is not None:
            self._rec.send_audio_frame(pcm)

    def stop(self) -> None:
        if self._rec is not None:
            self._rec.stop()


class DashScopeEngine:
    """dashscope SDK 薄封装，方便测试注入 fake 模块。"""

    # /api/tts 试听端点的音频 MIME：synthesize 返回 MP3
    SYNTH_MIME = "audio/mpeg"

    def __init__(self, api_key: str, tts_model: str = "cosyvoice-v2",
                 asr_model: str = "paraformer-realtime-v2"):
        import dashscope
        dashscope.api_key = api_key
        self.tts_model = tts_model
        self.asr_model = asr_model

    def create_stream_recognizer(self, on_event: Callable[[dict], None]) -> _DashScopeStreamSession:
        return _DashScopeStreamSession(self.asr_model, on_event)

    def transcribe_file(self, path: Path, fmt: str = "wav") -> str:
        from dashscope.audio.asr import Recognition
        # Fun-ASR 系列的 language_hints 仅支持 1 个语种，qwen/paraformer 支持多个
        if self.asr_model.startswith("fun-asr"):
            hints = ["zh"]
        else:
            hints = ["zh", "en"]
        rec = Recognition(
            model=self.asr_model, callback=None, format=fmt,
            sample_rate=16000, language_hints=hints,
        )
        result = rec.call(str(path))
        if result.status_code != HTTPStatus.OK:
            raise AudioError(f"语音识别失败：{result.message}")
        sentences = result.get_sentence() or []
        return "".join(s.get("text", "") for s in sentences if isinstance(s, dict)).strip()

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer
        synth = SpeechSynthesizer(
            model=self.tts_model, voice=voice,
            format=AudioFormat.MP3_22050HZ_MONO_256KBPS,
            speech_rate=speed,
        )
        data = synth.call(text)
        if not data:
            raise AudioError("语音合成结果为空")
        return bytes(data)

    def synthesize_stream(self, text: str, voice: str, speed: float,
                          on_chunk: Callable[[bytes], None]) -> None:
        """流式合成：音频块经 on_chunk 透出（SDK 回调线程调用），块为 22.05k 单声道 PCM16。"""
        from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

        class _Cb(ResultCallback):
            def on_data(self, data):
                on_chunk(bytes(data))

        synth = SpeechSynthesizer(
            model=self.tts_model, voice=voice,
            format=AudioFormat.PCM_22050HZ_MONO_16BIT,
            speech_rate=speed, callback=_Cb(),
        )
        synth.streaming_call(text)
        synth.streaming_complete()


class AudioService:
    """面向端点的服务层：临时文件生命周期与错误归一化。"""

    def __init__(self, engine):
        self.engine = engine
        self.available = True

    @classmethod
    def from_config(cls, cfg: dict) -> "AudioService | None":
        """按 INTERVIEW_AUDIO_PROVIDER 选引擎；未设置时有 DASHSCOPE_API_KEY 即 dashscope。"""
        provider = cfg.get("audio_provider") or (
            "dashscope" if cfg.get("dashscope_api_key") else ""
        )
        if provider == "dashscope":
            if not cfg.get("dashscope_api_key"):
                return None
            return cls(DashScopeEngine(
                cfg["dashscope_api_key"],
                tts_model=cfg.get("tts_model", "cosyvoice-v2"),
                asr_model=cfg.get("asr_model", "paraformer-realtime-v2"),
            ))
        if provider == "local":
            from .local_audio import LocalEngine
            return cls(LocalEngine.from_config(cfg))
        if not provider:
            return None
        raise ValueError(f"不支持的语音引擎 provider: {provider}")

    def transcribe(self, audio_bytes: bytes, fmt: str = "wav") -> str:
        if fmt not in ASR_FORMATS:
            raise AudioError(f"不支持的音频格式：{fmt}")
        suffix = f".{fmt}"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp = Path(f.name)
        try:
            return self.engine.transcribe_file(tmp, fmt)
        except AudioError:
            raise
        except Exception as e:  # noqa: BLE001 - SDK 异常统一包装
            raise AudioError(f"语音识别失败：{e}") from None
        finally:
            tmp.unlink(missing_ok=True)

    def synthesize(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        try:
            return self.engine.synthesize(text, voice, speed)
        except AudioError:
            raise
        except Exception as e:  # noqa: BLE001 - SDK 异常统一包装
            raise AudioError(f"语音合成失败：{e}") from None


class StreamRecognizer:
    """一个语音会话的流式识别器薄包装：识别协议由引擎的工厂方法实现。

    on_event 在引擎回调线程被调用，收到 dict：
      {"type": "partial"|"final"|"error", "text"/"message": str}
    """

    def __init__(self, engine, on_event: Callable[[dict], None]):
        if engine is None or on_event is None:
            raise TypeError("StreamRecognizer 需要 engine 与 on_event")
        self.engine = engine
        self.on_event = on_event
        self._session = None

    def start(self) -> None:
        self._session = self.engine.create_stream_recognizer(self.on_event)
        self._session.start()

    def feed(self, pcm: bytes) -> None:
        if self._session is not None:
            self._session.feed(pcm)

    def stop(self) -> None:
        if self._session is not None:
            self._session.stop()
