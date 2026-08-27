# interview_agent/web/audio.py
"""DashScope 语音服务：Paraformer 本地文件识别 + CosyVoice 合成。

dashscope SDK 在方法体内延迟 import：未安装/未使用语音的渠道不受影响。
"""
from __future__ import annotations
import tempfile
from http import HTTPStatus
from pathlib import Path

ASR_FORMATS = {"wav", "mp3", "opus", "speex", "aac", "amr"}


class AudioError(Exception):
    """语音识别/合成失败，上层转 HTTP 错误。"""


class DashScopeEngine:
    """dashscope SDK 薄封装，方便测试注入 fake 模块。"""

    def __init__(self, api_key: str, tts_model: str = "cosyvoice-v2",
                 asr_model: str = "paraformer-realtime-v2"):
        import dashscope
        dashscope.api_key = api_key
        self.tts_model = tts_model
        self.asr_model = asr_model

    def transcribe_file(self, path: Path, fmt: str = "wav") -> str:
        from dashscope.audio.asr import Recognition
        # Fun-ASR 系列的 language_hints 仅支持 1 个语种，qwen/paraformer 支持多个
        if self.asr_model.startswith("fun-asr"):
            hints = ["zh"]
        else:
            hints = ["zh", "en"]
        rec = Recognition(
            model=self.asr_model, format=fmt,
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


class AudioService:
    """面向端点的服务层：临时文件生命周期与错误归一化。"""

    def __init__(self, engine: DashScopeEngine):
        self.engine = engine
        self.available = True

    @classmethod
    def from_config(cls, api_key: str, tts_model: str = "cosyvoice-v2",
                    asr_model: str = "paraformer-realtime-v2") -> "AudioService | None":
        return cls(DashScopeEngine(api_key, tts_model=tts_model, asr_model=asr_model)) if api_key else None

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
