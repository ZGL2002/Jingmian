# Web 端语音/视频面试 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有 Web 面试增加语音模式：四风格（严肃/冷漠/温和/引导）切换动态背景+人设+TTS 音色，用户语音作答（ASR），面试官语音播报（TTS），整场混音存一个音频文件，摄像头仅预览。

**Architecture:** 复用现有 `InterviewTask` 会话循环与 SSE 事件流，语音作为现有会话的可选模式。新增风格模块 `persona.py`（core 层，prompt/transcript/TTS 音色共用）、DashScope 语音服务 `web/audio.py`（Paraformer Recognition 本地文件识别 + CosyVoice 合成）、7 个新端点；前端新增 `static/voice.js` 语音引擎（录音、WAV 编码、TTS 播放队列、整场混音 MediaRecorder），改造 `app.js`/`index.html`/`style.css`。

**Tech Stack:** Python 3.11+ / FastAPI / dashscope SDK（新增依赖）/ 原生 JS + Web Audio API + MediaRecorder。

**Spec:** `docs/superpowers/specs/2026-08-26-voice-video-interview-design.md`

## Global Constraints

- Python `>=3.11`；新依赖只允许新增 `dashscope`（pyproject 主依赖）。
- 风格 key 白名单：`serious` / `cold` / `gentle` / `guide`，默认 `serious`；未知 key 一律回落 `serious`。
- TTS 音色三级优先：`INTERVIEW_TTS_VOICE_<STYLE>`（按风格）> `INTERVIEW_TTS_VOICE`（全局）> persona 内置默认；TTS 模型 `INTERVIEW_TTS_MODEL` 默认 `cosyvoice-v2`。单音色账号只配全局音色即可，风格差异靠背景/人设/语速保留。
- 音频留档**只有一个文件**：`interviews/<user_id>/<session_id>/audio/interview.webm`（整场混音）。ASR/TTS 中间数据不落档（ASR 临时文件识别后立即删除）。
- 文字记录维持 `transcript.jsonl` 不变，仅 meta 行新增 `style` 字段；不改动对话行的结构。
- 摄像头视频不录制、不存储。
- 所有新端点走现有 `TokenAuthMiddleware` 认证；路径一律 SID 白名单 `[\w\-]+` + `resolve()` + 越界检查。
- 上传上限：ASR 20MB、整场音频 500MB、背景 50MB；TTS 单次文本 ≤500 字。
- UI 与文档文案用中文。
- 与 spec 的两处已核实偏差（随 Task 1 一并回写 spec）：① `persona.py` 放在 `interview_agent/` 顶层而非 `web/`（`session.py` 属 core 层，避免 core→web 反向依赖）；② ASR 用 `paraformer-realtime-v2` 的 `Recognition.call(本地文件)` 同步识别（录音文件转写接口要求公网 URL，不适用本地部署）；③ 音色初值更新为已核实的 `longshu_v2`/`longyingjing`/`longyingling`/`longxiaochun_v2`。

## DashScope 接口事实（已对官方文档核实，写代码时照此实现）

- **ASR**：`from dashscope.audio.asr import Recognition`；`Recognition(model="paraformer-realtime-v2", format=fmt, sample_rate=16000, language_hints=["zh","en"]).call(文件路径)` 同步返回；`result.status_code == 200` 时 `result.get_sentence()` 返回 `List[Dict]`，每项 `{"text": ...}`。format 仅支持 `pcm/wav/mp3/opus(Ogg封装)/speex/aac/amr`，**不支持 webm** —— 前端负责转 WAV。
- **TTS**：`from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat`；`SpeechSynthesizer(model="cosyvoice-v2", voice=..., format=AudioFormat.MP3_22050HZ_MONO_256KBPS, speech_rate=...)`，`synth.call(text)` 返回完整 mp3 bytes；每次调用需重新实例化；`speech_rate ∈ [0.5, 2.0]`。
- API key 通过 `dashscope.api_key = key` 设置。

---

### Task 1: 风格模块 `persona.py`

**Files:**
- Create: `interview_agent/persona.py`
- Modify: `docs/superpowers/specs/2026-08-26-voice-video-interview-design.md`（回写三处勘误：模块位置、ASR 接口、音色名）
- Test: `tests/test_persona.py`

**Interfaces:**
- Produces: `PersonaStyle`（frozen dataclass：`key: str, label: str, prompt_fragment: str, tts_voice: str, tts_speed: float`）；`get_style(key: str) -> PersonaStyle`（未知回落 serious）；`list_styles() -> list[PersonaStyle]`（固定顺序 serious/cold/gentle/guide）；`style_keys() -> set[str]`；`resolve_voice(style_key: str, global_voice: str = "", style_voices: dict[str, str] | None = None) -> str`（优先级：style_voices[key] > global_voice > 内置默认）。后续所有任务 import 自 `interview_agent.persona`。

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_persona.py -v`
Expected: FAIL（`ModuleNotFoundError: interview_agent.persona`）

- [ ] **Step 3: 实现**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_persona.py -v`
Expected: 8 PASS

- [ ] **Step 5: 回写 spec 勘误并提交**

spec 的"模块设计"节：`web/persona.py` 改为 `interview_agent/persona.py`（注明原因：core 层 session.py 需引用，避免 core→web 反向依赖）；"音频服务"节 ASR 描述改为"`paraformer-realtime-v2` Recognition 本地文件同步识别（录音文件转写接口要求公网 URL，不适用）"；音色初值改为本文档已核实四值。

```bash
git add interview_agent/persona.py tests/test_persona.py docs/superpowers/specs/2026-08-26-voice-video-interview-design.md
git commit -m "feat: 面试官风格模块 persona（人设提示词+TTS音色）"
```

---

### Task 2: 风格贯穿 session——提示词、配置、transcript meta

**Files:**
- Modify: `interview_agent/prompts.py:21-61`（`build_system_prompt` 加 `persona` 参数）
- Modify: `interview_agent/models.py:34-47`（`SessionConfig` 加 `style` 字段）
- Modify: `interview_agent/session.py:28-52`（`start()` 传 persona 与 style）
- Modify: `interview_agent/storage.py:46-52`（`init_transcript` 加 `style` 参数）
- Test: `tests/test_style_plumbing.py`

**Interfaces:**
- Consumes: `interview_agent.persona.get_style`（Task 1）
- Produces: `build_system_prompt(..., persona: str = "")`；`SessionConfig.style: str = "serious"`；`init_transcript(path, user_id, session_id, company="", position="", style="")`，style 非空时 meta 行含 `"style"` 键。CLI/飞书渠道不传 style，走默认值，行为不变。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_style_plumbing.py
from pathlib import Path
from interview_agent.models import SessionConfig
from interview_agent.prompts import build_system_prompt
from interview_agent.session import InterviewSession
from interview_agent.storage import read_jsonl


def test_prompt_contains_persona():
    p = build_system_prompt(None, persona="语气沉稳严厉，追问犀利")
    assert "语气沉稳严厉" in p


def test_prompt_without_persona_has_no_block():
    assert "风格人设" not in build_system_prompt(None)


def test_session_style_default_serious(tmp_path):
    cfg = SessionConfig(user_id="u1", session_root=tmp_path)
    s = InterviewSession(cfg)
    s.start()
    meta = read_jsonl(s.transcript_path)[0]
    assert meta["style"] == "serious"
    prompt = (s.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "严肃、专业" in prompt  # serious 人设片段


def test_session_style_cold(tmp_path):
    cfg = SessionConfig(user_id="u1", session_root=tmp_path, style="cold")
    s = InterviewSession(cfg)
    s.start()
    meta = read_jsonl(s.transcript_path)[0]
    assert meta["style"] == "cold"
    prompt = (s.session_dir / "prompt.md").read_text(encoding="utf-8")
    assert "公事公办" in prompt  # cold 人设片段
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_style_plumbing.py -v`
Expected: FAIL（`build_system_prompt` 无 `persona` 参数 / `SessionConfig` 无 `style`）

- [ ] **Step 3: 实现**

`prompts.py`——签名加参数，f-string 首段后插人人设块：

```python
def build_system_prompt(
    resume: ResumeDocument | None,
    language: str = "zh",
    min_questions: int = 10,
    company: str = "",
    position: str = "",
    jd_text: str | None = None,
    experience_refs: list | None = None,
    persona: str = "",
) -> str:
```

f-string 开头改为（只动前两行，其余不变）：

```python
    persona_block = f"当前面试官风格人设（语气与追问方式必须贯彻）：{persona}\n\n" if persona else ""
    return f"""你是一位资深后端技术面试官，正在进行一场真实感的一对一技术面试。

{persona_block}面试规则：
```

（`persona_block` 为空时该行输出一个空行，与现状视觉等价，无需额外处理。）

`models.py`——`SessionConfig` 末尾加字段：

```python
    experience_refs: list[ExperienceEntry] = field(default_factory=list)
    style: str = "serious"
```

`storage.py`——`init_transcript` 加参数与写入：

```python
def init_transcript(path: Path, user_id: str, session_id: str, company: str = "", position: str = "",
                    style: str = "") -> None:
    entry = {"role": "meta", "user_id": user_id, "session_id": session_id}
    if company:
        entry["company"] = company
    if position:
        entry["position"] = position
    if style:
        entry["style"] = style
    append_jsonl(path, entry)
```

`session.py`——顶部 `from .persona import get_style`；`start()` 内两处调用改为：

```python
        init_transcript(
            self.transcript_path,
            self.config.user_id,
            self.session_id,
            company=self.config.company,
            position=self.config.position,
            style=self.config.style,
        )
        prompt = build_system_prompt(
            self.resume,
            self.config.language,
            min_questions=self.config.min_questions,
            company=self.config.company,
            position=self.config.position,
            jd_text=self.config.jd_text or None,
            experience_refs=self.config.experience_refs,
            persona=get_style(self.config.style).prompt_fragment,
        )
```

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `python -m pytest tests/test_style_plumbing.py tests/test_prompts.py tests/test_prompts_extra.py tests/test_session.py tests/test_session_web.py tests/test_storage.py -v`
Expected: 全部 PASS（现有用例不受影响）

- [ ] **Step 5: 提交**

```bash
git add interview_agent/prompts.py interview_agent/models.py interview_agent/session.py interview_agent/storage.py tests/test_style_plumbing.py
git commit -m "feat: 面试官风格注入系统提示词并记入 transcript meta"
```

---

### Task 3: Web 层风格——start 表单字段 + /api/styles

**Files:**
- Modify: `interview_agent/web/manager.py:30-46`（`start_session` 加 `style`）
- Modify: `interview_agent/web/app.py:70-105`（start 端点加 `style` Form；新增 `/api/styles`）
- Test: `tests/test_web_styles.py`

**Interfaces:**
- Consumes: `persona.get_style/list_styles`（Task 1）；`SessionConfig.style`（Task 2）
- Produces: `SessionManager.start_session(user_id, *, company="", position="", resume_text=None, jd_text="", experiences=None, style="serious") -> str`（未知 style 归一化为 serious）；`GET /api/styles` 返回 `[{"key","label"}, ...]`；`POST /api/session/start` 接受表单字段 `style`。前端 Task 8-10 依赖这两个接口。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_styles.py
import time
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
from interview_agent.llm import AssistantTurn
from interview_agent.storage import read_jsonl
from pathlib import Path


class AppLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        from interview_agent.llm import StreamEnd
        yield StreamEnd(turn)


def make_client(tmp_path):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    llm = AppLLM([AssistantTurn(content="开场"), AssistantTurn(content="问一"), AssistantTurn(content="## 报告")])
    return TestClient(create_app(cfg, llm=llm))


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def login(c):
    c.post("/api/login", json={"token": "secret"})


def test_styles_endpoint(tmp_path):
    c = make_client(tmp_path)
    login(c)
    data = c.get("/api/styles").json()
    assert [s["key"] for s in data] == ["serious", "cold", "gentle", "guide"]
    assert data[0]["label"] == "严肃"


def test_start_with_style_cold(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = c.post("/api/session/start", data={"style": "cold"}).json()["session_id"]
    session_dir = Path(tmp_path) / "local" / sid
    meta = read_jsonl(session_dir / "transcript.jsonl")[0]
    assert meta["style"] == "cold"
    assert "公事公办" in (session_dir / "prompt.md").read_text(encoding="utf-8")


def test_start_with_unknown_style_falls_back(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = c.post("/api/session/start", data={"style": "hacker"}).json()["session_id"]
    meta = read_jsonl(Path(tmp_path) / "local" / sid / "transcript.jsonl")[0]
    assert meta["style"] == "serious"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_web_styles.py -v`
Expected: FAIL（`/api/styles` 404；start 不认识 style）

- [ ] **Step 3: 实现**

`manager.py`——`start_session` 加参数并传给 SessionConfig（顶部 `from ..persona import get_style`）：

```python
    def start_session(self, user_id: str, *, company: str = "", position: str = "",
                      resume_text: str | None = None, jd_text: str = "",
                      experiences=None, style: str = "serious") -> str:
        ...
        cfg = SessionConfig(
            ...,
            experience_refs=list(experiences or []),
            style=get_style(style).key,
        )
```

`app.py`——顶部 `from ..persona import list_styles`；start 端点签名加 `style: str = Form("serious")` 并传 `style=style`；新增端点（放在 `/api/logout` 之后）：

```python
    @app.get("/api/styles")
    def styles():
        return [{"key": s.key, "label": s.label} for s in list_styles()]
```

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `python -m pytest tests/test_web_styles.py tests/test_web_app.py tests/test_web_manager.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add interview_agent/web/manager.py interview_agent/web/app.py tests/test_web_styles.py
git commit -m "feat: Web 端开始面试支持选择面试官风格"
```

---

### Task 4: DashScope 语音服务 `web/audio.py` + 依赖与配置

**Files:**
- Create: `interview_agent/web/audio.py`
- Modify: `pyproject.toml`（dependencies 加 `dashscope`）
- Modify: `interview_agent/config.py:17-34`（`load_config` 加 `dashscope_api_key`）
- Test: `tests/test_web_audio_service.py`

**Interfaces:**
- Produces: `AudioError(Exception)`；`DashScopeEngine(api_key, tts_model="cosyvoice-v2")`（`.transcribe_file(path, fmt="wav") -> str`、`.synthesize(text, voice, speed) -> bytes`，dashscope 延迟 import，合成用构造传入的模型名）；`AudioService(engine)`（`.transcribe(audio_bytes, fmt="wav") -> str`、`.synthesize(text, voice, speed) -> bytes`、`.available` 属性恒 True）；`AudioService.from_config(api_key: str, tts_model: str = "cosyvoice-v2") -> AudioService | None`（空 key 返回 None）。Task 5 的端点与 `__main__.py` 装配消费这些签名。
- config 新键：`cfg["dashscope_api_key"]`（独立于 INTERVIEW_PROVIDER，DeepSeek LLM + DashScope 语音可并用）、`cfg["tts_model"]`（默认 `cosyvoice-v2`）、`cfg["tts_voice"]`（默认空=用内置四音色）、`cfg["tts_voice_by_style"]`（扫描 `INTERVIEW_TTS_VOICE_*` 环境变量得到的 `{style_key_lower: voice}`，如 `INTERVIEW_TTS_VOICE_SERIOUS=x` → `{"serious": "x"}`；注意 `INTERVIEW_TTS_VOICE` 本身不入此 dict）。

- [ ] **Step 1: 写失败测试（fake dashscope 模块注入，不触网）**

```python
# tests/test_web_audio_service.py
import sys
import tempfile
import types
from pathlib import Path
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


def test_service_wraps_unexpected_errors(tmp_path, monkeypatch):
    class BoomRecognition:
        def __init__(self, **kwargs): pass
        def call(self, path): raise RuntimeError("网络断了")
    install_fake_dashscope(monkeypatch, asr_cls=BoomRecognition)
    svc = AudioService(DashScopeEngine("sk-test"))
    with pytest.raises(AudioError, match="语音识别失败"):
        svc.transcribe(b"BYTES")


def test_from_config_empty_key_is_none():
    assert AudioService.from_config("") is None
    assert AudioService.from_config("sk-x") is not None


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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_web_audio_service.py -v`
Expected: FAIL（`interview_agent.web.audio` 不存在）

- [ ] **Step 3: 实现**

```python
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

    def __init__(self, api_key: str, tts_model: str = "cosyvoice-v2"):
        import dashscope
        dashscope.api_key = api_key
        self.tts_model = tts_model

    def transcribe_file(self, path: Path, fmt: str = "wav") -> str:
        from dashscope.audio.asr import Recognition
        rec = Recognition(
            model="paraformer-realtime-v2", format=fmt,
            sample_rate=16000, language_hints=["zh", "en"],
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
    def from_config(cls, api_key: str, tts_model: str = "cosyvoice-v2") -> "AudioService | None":
        return cls(DashScopeEngine(api_key, tts_model=tts_model)) if api_key else None

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
```

`pyproject.toml` dependencies 列表加一行 `"dashscope>=1.20",`（放在 `lark-oapi` 之后）。

`config.py` `load_config` 返回 dict 加：

```python
        "dashscope_api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "tts_model": os.environ.get("INTERVIEW_TTS_MODEL", "cosyvoice-v2"),
        "tts_voice": os.environ.get("INTERVIEW_TTS_VOICE", ""),
        # INTERVIEW_TTS_VOICE_<STYLE>（如 _SERIOUS）按风格覆盖；前缀恰好等于
        # INTERVIEW_TTS_VOICE 的全局键本身（无后缀）不在此 dict 中
        "tts_voice_by_style": {
            k[len("INTERVIEW_TTS_VOICE_"):].lower(): v
            for k, v in os.environ.items()
            if k.startswith("INTERVIEW_TTS_VOICE_") and v
        },
```

- [ ] **Step 4: 安装依赖并运行确认通过**

Run: `python -m pip install -e ".[dev]" && python -m pytest tests/test_web_audio_service.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add interview_agent/web/audio.py pyproject.toml interview_agent/config.py tests/test_web_audio_service.py
git commit -m "feat: DashScope 语音服务（Paraformer 识别 + CosyVoice 合成）"
```

---

### Task 5: /api/asr 与 /api/tts 端点 + Web 入口装配

**Files:**
- Modify: `interview_agent/web/app.py`（`create_app` 加 `audio` 参数；新增两端点；import 补充）
- Modify: `interview_agent/web/__main__.py`（构造 AudioService 并传入）
- Test: `tests/test_web_audio_api.py`

**Interfaces:**
- Consumes: `AudioService/AudioError/ASR_FORMATS`（Task 4）；`persona.get_style/resolve_voice`（Task 1）
- Produces: `create_app(config, llm=None, audio=None)`（第三参默认 None，旧调用不破坏）；`POST /api/asr`（multipart：`file` 文件 + `fmt` 表单，默认 wav）→ `{"text": str}`，音频未启用 404、格式/大小非法 400、SDK 失败 502；`POST /api/tts`（JSON `{text, style}`）→ `audio/mpeg` 二进制响应，错误码同上、空文本/超 500 字 400。TTS 音色按三级优先解析：`config["tts_voice_by_style"][style]` > `config["tts_voice"]` > persona 内置默认（语速仍取 persona 的 `tts_speed`）。前端 Task 9 消费这两个契约。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_audio_api.py
import io
import time
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
from interview_agent.web.audio import AudioError
from interview_agent.llm import AssistantTurn


class AppLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        from interview_agent.llm import StreamEnd
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


class FakeEngine:
    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def transcribe_file(self, path, fmt):
        self.calls.append(("asr", fmt))
        if self.fail == "asr":
            raise AudioError("识别失败：mock")
        return "识别出的文字"

    def synthesize(self, text, voice, speed):
        self.calls.append(("tts", text, voice, speed))
        if self.fail == "tts":
            raise AudioError("合成失败：mock")
        return b"MP3BYTES"


def make_client(tmp_path, engine=None, extra_cfg=None):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    if extra_cfg:
        cfg.update(extra_cfg)
    llm = AppLLM([AssistantTurn(content="开场")])
    audio = None
    if engine is not None:
        from interview_agent.web.audio import AudioService
        audio = AudioService(engine)
    return TestClient(create_app(cfg, llm=llm, audio=audio))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def test_asr_roundtrip(tmp_path):
    eng = FakeEngine()
    c = make_client(tmp_path, engine=eng)
    login(c)
    r = c.post("/api/asr", files={"file": ("a.wav", io.BytesIO(b"WAVDATA"), "audio/wav")},
               data={"fmt": "wav"})
    assert r.status_code == 200
    assert r.json() == {"text": "识别出的文字"}
    assert eng.calls == [("asr", "wav")]


def test_tts_roundtrip_resolves_style_voice(tmp_path):
    eng = FakeEngine()
    c = make_client(tmp_path, engine=eng)
    login(c)
    r = c.post("/api/tts", json={"text": "你好。", "style": "cold"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.content == b"MP3BYTES"
    assert eng.calls == [("tts", "你好。", "longyingjing", 0.95)]


def test_tts_voice_overrides(tmp_path):
    # 单音色账号：全局 INTERVIEW_TTS_VOICE 对所有风格生效，语速仍按风格
    eng = FakeEngine()
    c = make_client(tmp_path, engine=eng, extra_cfg={"tts_voice": "single-voice"})
    login(c)
    c.post("/api/tts", json={"text": "你好。", "style": "cold"})
    assert eng.calls[-1] == ("tts", "你好。", "single-voice", 0.95)
    # 按风格覆盖优先于全局
    eng2 = FakeEngine()
    c2 = make_client(tmp_path, engine=eng2, extra_cfg={
        "tts_voice": "single-voice", "tts_voice_by_style": {"serious": "serious-voice"}})
    login(c2)
    c2.post("/api/tts", json={"text": "你好。", "style": "serious"})
    assert eng2.calls[-1][2] == "serious-voice"
    c2.post("/api/tts", json={"text": "你好。", "style": "guide"})
    assert eng2.calls[-1][2] == "single-voice"


def test_tts_text_limits(tmp_path):
    c = make_client(tmp_path, engine=FakeEngine())
    login(c)
    assert c.post("/api/tts", json={"text": "", "style": "serious"}).status_code == 400
    assert c.post("/api/tts", json={"text": "长" * 501, "style": "serious"}).status_code == 400


def test_audio_disabled_returns_404(tmp_path):
    c = make_client(tmp_path)  # audio=None
    login(c)
    assert c.post("/api/tts", json={"text": "x", "style": "serious"}).status_code == 404
    assert c.post("/api/asr", files={"file": ("a.wav", io.BytesIO(b"x"), "audio/wav")}).status_code == 404


def test_asr_bad_format_and_size(tmp_path):
    c = make_client(tmp_path, engine=FakeEngine())
    login(c)
    r = c.post("/api/asr", files={"file": ("a.webm", io.BytesIO(b"x"), "audio/webm")},
               data={"fmt": "webm"})
    assert r.status_code == 400
    big = io.BytesIO(b"\0" * (20 * 1024 * 1024 + 1))
    r = c.post("/api/asr", files={"file": ("a.wav", big, "audio/wav")}, data={"fmt": "wav"})
    assert r.status_code == 400


def test_sdk_error_maps_502(tmp_path):
    c = make_client(tmp_path, engine=FakeEngine(fail="tts"))
    login(c)
    assert c.post("/api/tts", json={"text": "你好。", "style": "serious"}).status_code == 502
    c2 = make_client(tmp_path, engine=FakeEngine(fail="asr"))
    login(c2)
    r = c2.post("/api/asr", files={"file": ("a.wav", io.BytesIO(b"x"), "audio/wav")})
    assert r.status_code == 502
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_web_audio_api.py -v`
Expected: FAIL（`create_app` 无 `audio` 参数 / 端点 404）

- [ ] **Step 3: 实现**

`app.py`：

签名改 `def create_app(config: dict, llm=None, audio=None) -> FastAPI:`；import 区补：

```python
from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile, File
from ..persona import get_style, resolve_voice
from .audio import ASR_FORMATS, AudioError
```

`/api/logout` 之后新增：

```python
    @app.post("/api/asr")
    async def asr(file: UploadFile = File(...), fmt: str = Form("wav")):
        if audio is None:
            raise HTTPException(404, "语音功能未启用（未配置 DASHSCOPE_API_KEY）")
        if fmt not in ASR_FORMATS:
            raise HTTPException(400, "不支持的音频格式")
        data = await file.read()
        if not data:
            raise HTTPException(400, "音频为空")
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(400, "音频过大（限 20MB）")
        try:
            text = audio.transcribe(data, fmt)
        except AudioError as e:
            raise HTTPException(502, str(e))
        return {"text": text}

    @app.post("/api/tts")
    def tts(payload: dict):
        if audio is None:
            raise HTTPException(404, "语音功能未启用（未配置 DASHSCOPE_API_KEY）")
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 500:
            raise HTTPException(400, "文本为空或超过 500 字")
        style = get_style(str(payload.get("style", "")))
        voice = resolve_voice(
            style.key,
            config.get("tts_voice", ""),
            config.get("tts_voice_by_style") or None,
        )
        try:
            mp3 = audio.synthesize(text, voice, style.tts_speed)
        except AudioError as e:
            raise HTTPException(502, str(e))
        return Response(content=mp3, media_type="audio/mpeg")
```

（格式白名单在端点入口校验，非法格式 400；SDK 调用失败统一 AudioError → 502。）

`web/__main__.py`——装配：

```python
from .audio import AudioService
...
    app = create_app(
        cfg, llm=llm,
        audio=AudioService.from_config(cfg.get("dashscope_api_key", ""), tts_model=cfg.get("tts_model", "cosyvoice-v2")),
    )
```

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `python -m pytest tests/test_web_audio_api.py tests/test_web_app.py tests/test_web_styles.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add interview_agent/web/app.py interview_agent/web/__main__.py tests/test_web_audio_api.py
git commit -m "feat: /api/asr 与 /api/tts 端点（DashScope 语音，未配置时 404 降级）"
```

---

### Task 6: 整场音频存储端点 + 历史 has_audio

**Files:**
- Modify: `interview_agent/storage.py`（新增 `save_session_audio`；`list_sessions` 加 `has_audio`）
- Modify: `interview_agent/web/app.py`（`GET/POST /api/sessions/{sid}/audio`）
- Test: `tests/test_web_session_audio.py`

**Interfaces:**
- Consumes: `app._session_path` 路径防护（现有）
- Produces: `storage.save_session_audio(session_dir: Path, data: bytes) -> Path`（写 `audio/interview.webm`，覆盖旧文件）；`list_sessions` 每项新增 `"has_audio": bool`；`POST /api/sessions/{sid}/audio`（multipart `file`）→ `{"ok": True, "url": "/api/sessions/{sid}/audio"}`；`GET` 同路径 → `audio/webm` 文件（404 若不存在）。前端 Task 9 上传、Task 10 历史回放消费。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_session_audio.py
import io
import time
from pathlib import Path
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
from interview_agent.llm import AssistantTurn, StreamEnd


class AppLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def make_client(tmp_path):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    llm = AppLLM([AssistantTurn(content="开场")])
    return TestClient(create_app(cfg, llm=llm))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def start_session(c):
    return c.post("/api/session/start", data={}).json()["session_id"]


def test_upload_and_download_roundtrip(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    r = c.post(f"/api/sessions/{sid}/audio",
               files={"file": ("interview.webm", io.BytesIO(b"WEBMAUDIO"), "audio/webm")})
    assert r.status_code == 200
    assert r.json()["url"] == f"/api/sessions/{sid}/audio"
    assert (Path(tmp_path) / "local" / sid / "audio" / "interview.webm").read_bytes() == b"WEBMAUDIO"

    g = c.get(f"/api/sessions/{sid}/audio")
    assert g.status_code == 200
    assert g.content == b"WEBMAUDIO"
    assert g.headers["content-type"].startswith("audio/webm")


def test_download_before_upload_404(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    assert c.get(f"/api/sessions/{sid}/audio").status_code == 404


def test_invalid_sid_400(tmp_path):
    c = make_client(tmp_path)
    login(c)
    assert c.get("/api/sessions/..%2F..%2Fetc/audio").status_code in (400, 404)


def test_history_has_audio_flag(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    assert c.get("/api/history").json()[0]["has_audio"] is False
    c.post(f"/api/sessions/{sid}/audio",
           files={"file": ("interview.webm", io.BytesIO(b"X"), "audio/webm")})
    assert c.get("/api/history").json()[0]["has_audio"] is True


def test_empty_upload_400(tmp_path):
    c = make_client(tmp_path)
    login(c)
    sid = start_session(c)
    r = c.post(f"/api/sessions/{sid}/audio",
               files={"file": ("interview.webm", io.BytesIO(b""), "audio/webm")})
    assert r.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_web_session_audio.py -v`
Expected: FAIL（端点 404 / `has_audio` KeyError）

- [ ] **Step 3: 实现**

`storage.py`——`save_report` 之后新增：

```python
def save_session_audio(session_dir: Path, data: bytes) -> Path:
    """整场面试混音留档：audio/interview.webm（一场一个文件，覆盖旧值）。"""
    d = session_dir / "audio"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "interview.webm"
    p.write_bytes(data)
    return p
```

`list_sessions` 的 `out.append({...})` 加一行：

```python
            "has_audio": (d / "audio" / "interview.webm").exists(),
```

`app.py`——`transcript` 端点之后新增（import `save_session_audio`）：

```python
    @app.post("/api/sessions/{session_id}/audio")
    async def upload_audio(session_id: str, file: UploadFile = File(...)):
        session_dir = _session_path(session_id)
        data = await file.read()
        if not data:
            raise HTTPException(400, "音频为空")
        if len(data) > 500 * 1024 * 1024:
            raise HTTPException(400, "音频过大（限 500MB）")
        save_session_audio(session_dir, data)
        return {"ok": True, "url": f"/api/sessions/{session_id}/audio"}

    @app.get("/api/sessions/{session_id}/audio")
    def download_audio(session_id: str):
        p = _session_path(session_id) / "audio" / "interview.webm"
        if not p.is_file():
            raise HTTPException(404, "音频不存在")
        return FileResponse(p, media_type="audio/webm")
```

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `python -m pytest tests/test_web_session_audio.py tests/test_storage.py tests/test_web_app.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add interview_agent/storage.py interview_agent/web/app.py tests/test_web_session_audio.py
git commit -m "feat: 整场面试音频存档端点（audio/interview.webm）与历史 has_audio"
```

---

### Task 7: 自定义动态背景端点

**Files:**
- Modify: `interview_agent/web/app.py`（`GET/POST /api/backgrounds/{style}`）
- Test: `tests/test_web_backgrounds.py`

**Interfaces:**
- Consumes: `persona.style_keys`（Task 1）；`WEB_USER_ID` 用户命名空间
- Produces: `POST /api/backgrounds/{style}`（multipart `file`，扩展名白名单 gif/mp4/webm/png，≤50MB）→ `{"ok": True, "url": ...}`，上传时删除同风格旧文件；`GET` 同路径 → 文件或 404（前端回落 CSS 主题）。存储位置 `interviews/<uid>/backgrounds/<style>.<ext>`。前端 Task 10 消费。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_backgrounds.py
import io
from pathlib import Path
from fastapi.testclient import TestClient
from interview_agent.web.app import create_app
from interview_agent.llm import AssistantTurn, StreamEnd


class AppLLM:
    def __init__(self, script):
        self.script = list(script)

    def chat(self, messages, tools=None, max_tokens=None):
        return self.script.pop(0)

    def chat_stream(self, messages, tools=None, max_tokens=None):
        turn = self.script.pop(0)
        if turn.content:
            yield turn.content
        yield StreamEnd(turn)


def make_client(tmp_path):
    cfg = {
        "session_root": str(tmp_path), "min_questions": 1, "language": "zh",
        "model": "deepseek-chat", "web_token": "secret",
    }
    return TestClient(create_app(cfg, llm=AppLLM([AssistantTurn(content="开场")])))


def login(c):
    c.post("/api/login", json={"token": "secret"})


def upload(c, style, name, data, mime):
    return c.post(f"/api/backgrounds/{style}",
                  files={"file": (name, io.BytesIO(data), mime)})


def test_upload_get_and_replace(tmp_path):
    c = make_client(tmp_path)
    login(c)
    r = upload(c, "serious", "bg.gif", b"GIF89a", "image/gif")
    assert r.status_code == 200
    assert r.json()["url"] == "/api/backgrounds/serious"
    assert (Path(tmp_path) / "local" / "backgrounds" / "serious.gif").exists()

    g = c.get("/api/backgrounds/serious")
    assert g.status_code == 200
    assert g.content == b"GIF89a"

    # 换 mp4 后旧 gif 被清理，GET 返回新文件
    upload(c, "serious", "bg.mp4", b"MP4DATA", "video/mp4")
    assert not (Path(tmp_path) / "local" / "backgrounds" / "serious.gif").exists()
    assert c.get("/api/backgrounds/serious").content == b"MP4DATA"


def test_missing_background_404(tmp_path):
    c = make_client(tmp_path)
    login(c)
    assert c.get("/api/backgrounds/gentle").status_code == 404


def test_unknown_style_and_bad_ext_400(tmp_path):
    c = make_client(tmp_path)
    login(c)
    assert upload(c, "evil", "x.gif", b"X", "image/gif").status_code == 400
    assert upload(c, "gentle", "x.txt", b"X", "text/plain").status_code == 400


def test_backgrounds_dir_not_in_history(tmp_path):
    c = make_client(tmp_path)
    login(c)
    upload(c, "serious", "bg.gif", b"GIF89a", "image/gif")
    assert all(s["session_id"] != "backgrounds" for s in c.get("/api/history").json())
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_web_backgrounds.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 实现**

`app.py`——`remove_experience` 之后新增（import `style_keys`）：

```python
    BG_EXTS = {".gif", ".mp4", ".webm", ".png"}

    def _bg_dir() -> Path:
        d = (Path(config["session_root"]) / WEB_USER_ID / "backgrounds").resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    @app.post("/api/backgrounds/{style}")
    async def upload_background(style: str, file: UploadFile = File(...)):
        if style not in style_keys():
            raise HTTPException(400, "未知的面试官风格")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in BG_EXTS:
            raise HTTPException(400, "仅支持 gif/mp4/webm/png")
        data = await file.read()
        if not data or len(data) > 50 * 1024 * 1024:
            raise HTTPException(400, "文件为空或超过 50MB")
        d = _bg_dir()
        for old in d.glob(f"{style}.*"):
            old.unlink(missing_ok=True)
        (d / f"{style}{suffix}").write_bytes(data)
        return {"ok": True, "url": f"/api/backgrounds/{style}"}

    @app.get("/api/backgrounds/{style}")
    def get_background(style: str):
        if style not in style_keys():
            raise HTTPException(400, "未知的面试官风格")
        for p in sorted(_bg_dir().glob(f"{style}.*")):
            return FileResponse(p)
        raise HTTPException(404, "未设置自定义背景")
```

- [ ] **Step 4: 运行确认通过（含回归）**

Run: `python -m pytest tests/test_web_backgrounds.py tests/test_web_app.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add interview_agent/web/app.py tests/test_web_backgrounds.py
git commit -m "feat: 按风格上传自定义动态背景（内置 CSS 主题的覆盖机制）"
```

---

### Task 8: 前端结构与四套动态主题（HTML + CSS）

**Files:**
- Modify: `interview_agent/web/static/index.html`（风格选择、语音开关、背景上传、舞台层、摄像头窗、录音/重听按钮、引入 voice.js）
- Modify: `interview_agent/web/static/style.css`（全量重写：保留现有规则 + 四主题动画 + 新元素样式）
- Create: `interview_agent/web/static/voice.js`（本任务只建骨架，Task 9 填实现——为保持页面可加载无 JS 错误）

**Interfaces:**
- Consumes: `GET /api/styles`（Task 3）
- Produces: DOM 契约（Task 9/10 依赖的 id）：`cfg-styles`（风格选择容器）、`cfg-voice`（语音模式 checkbox）、`cfg-bg-file`+`btn-bg-upload`（背景上传）、`stage`（舞台容器，`data-theme` 属性驱动主题）、`bg-layer`（自定义背景层）、`cam-preview`（摄像头 video）、`btn-record`（语音作答按钮）、`btn-replay`（重听按钮，JS 动态生成）；`window.VoiceEngine`（Task 9）。

- [ ] **Step 1: 重写 index.html**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>面试 Agent</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav>
  <span class="brand">面试 Agent</span>
  <button data-view="interview">新面试</button>
  <button data-view="history">历史</button>
  <button data-view="experiences">面经库</button>
  <button id="logout">退出</button>
</nav>
<main>
  <section id="view-interview">
    <details id="config-panel" open>
      <summary>面试配置</summary>
      <div class="config-grid">
        <label>目标公司<input id="cfg-company" placeholder="如：字节跳动"></label>
        <label>岗位名称<input id="cfg-position" placeholder="如：后端开发"></label>
      </div>
      <div class="style-pick"><span>面试官风格：</span><div id="cfg-styles"></div></div>
      <label class="voice-toggle">
        <input type="checkbox" id="cfg-voice">
        语音模式（麦克风作答、面试官语音播报、开启摄像头画面）
      </label>
      <label>简历文本<textarea id="cfg-resume" placeholder="粘贴简历，或使用下方文件上传"></textarea></label>
      <label>上传简历文件<input type="file" id="cfg-resume-file" accept=".txt,.md,.pdf"></label>
      <label>岗位描述 JD<textarea id="cfg-jd" placeholder="粘贴岗位描述（可选）"></textarea></label>
      <div class="exp-pick"><span>面经参考：</span><div id="cfg-experiences"></div></div>
      <div class="bg-upload">
        为当前所选风格上传自定义动态背景（可选，gif/mp4/webm/png，不传则用内置动画）：
        <input type="file" id="cfg-bg-file" accept=".gif,.mp4,.webm,.png">
        <button id="btn-bg-upload" type="button">上传背景</button>
        <span id="bg-upload-msg"></span>
      </div>
      <button id="btn-start">开始面试</button>
      <span id="start-error" class="error"></span>
    </details>
    <div id="stage" data-theme="serious">
      <div id="bg-layer" class="hidden"></div>
      <video id="cam-preview" class="hidden" muted autoplay playsinline></video>
      <div id="chat"></div>
    </div>
    <div id="thinking" class="thinking hidden">面试官思考中…</div>
    <div class="answer-row">
      <button id="btn-record" class="hidden" disabled>🎤 开始作答</button>
      <textarea id="answer-input" placeholder="输入回答，Enter 发送，Shift+Enter 换行" disabled></textarea>
      <button id="btn-send" disabled>发送</button>
      <button id="btn-end" disabled>结束面试</button>
    </div>
  </section>
  <section id="view-history" class="hidden"></section>
  <section id="view-experiences" class="hidden"></section>
</main>
<script src="/static/voice.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: 重写 style.css**

```css
:root {
  --fg: #1c2333; --muted: #6b7280; --card: #ffffff; --line: #d7dce5;
  --accent: #2563eb; --bubble-int: #e8eefc; --bubble-cand: #eaf7ef;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "PingFang SC", "Microsoft YaHei", sans-serif; color: var(--fg); background: #f3f5f9; }
nav { display: flex; gap: 8px; align-items: center; padding: 8px 16px; background: #111a2e; }
nav .brand { color: #fff; font-weight: 700; margin-right: 12px; }
nav button { background: #2a3a5c; color: #fff; border: 0; border-radius: 6px; padding: 6px 14px; cursor: pointer; }
nav button:hover { background: #3a4f7c; }
main { max-width: 900px; margin: 0 auto; padding: 16px; }
.hidden { display: none !important; }
.error { color: #d0342c; }
details { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; margin-bottom: 14px; }
summary { cursor: pointer; font-weight: 600; }
.config-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 10px 0; }
label { display: block; margin: 6px 0; font-size: 14px; color: var(--muted); }
input[type="text"], input:not([type]), textarea { width: 100%; padding: 8px; border: 1px solid var(--line); border-radius: 6px; font: inherit; color: var(--fg); }
textarea { min-height: 90px; resize: vertical; }
button { font: inherit; }
details button { background: var(--accent); color: #fff; border: 0; border-radius: 6px; padding: 8px 18px; margin-top: 8px; cursor: pointer; }
.style-pick { margin: 10px 0; font-size: 14px; color: var(--muted); }
#cfg-styles { display: inline-flex; gap: 6px; flex-wrap: wrap; }
#cfg-styles label { display: inline-flex; align-items: center; gap: 4px; margin: 0; padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px; cursor: pointer; color: var(--fg); }
#cfg-styles label:has(input:checked) { border-color: var(--accent); background: var(--bubble-int); }
.voice-toggle { display: flex; align-items: center; gap: 6px; color: var(--fg); }
.bg-upload { margin: 8px 0; font-size: 13px; color: var(--muted); }
.bg-upload button { background: #5b6577 !important; }

#stage { position: relative; border-radius: 12px; min-height: 320px; max-height: 60vh; overflow: auto; padding: 14px; border: 1px solid var(--line); }
#bg-layer { position: sticky; top: 0; height: 0; z-index: 0; }
#bg-layer img, #bg-layer video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: .45; pointer-events: none; }
#cam-preview { position: sticky; top: 8px; float: right; width: 180px; height: auto; border-radius: 8px; border: 1px solid rgba(255,255,255,.55); background: #000; z-index: 3; margin-left: 10px; }
#chat { position: relative; z-index: 1; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 78%; padding: 8px 12px; border-radius: 10px; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.msg.interviewer { background: rgba(255,255,255,.92); border: 1px solid var(--line); align-self: flex-start; }
.msg.candidate { background: rgba(37,99,235,.14); align-self: flex-end; }
.msg.system { align-self: center; color: var(--muted); font-size: 13px; }
.msg .replay-btn { display: block; margin-top: 6px; font-size: 12px; color: var(--accent); background: none; border: none; cursor: pointer; padding: 0; }
.thinking { color: var(--muted); font-size: 13px; margin: 8px 2px; }
.answer-row { display: flex; gap: 8px; margin-top: 10px; }
.answer-row textarea { flex: 1; min-height: 64px; }
.answer-row button, #btn-record { border: 0; border-radius: 6px; padding: 8px 16px; cursor: pointer; align-self: flex-end; background: var(--accent); color: #fff; }
#btn-send { background: #16a34a; }
#btn-record { background: #7c3aed; }
#btn-record.recording { background: #d0342c; animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: .6; } }

/* 四套内置动态主题：stage[data-theme] 驱动 */
#stage[data-theme="serious"] { background: linear-gradient(160deg, #0b1f3a 0%, #14356b 50%, #0b1f3a 100%); background-size: 200% 200%; animation: theme-serious 14s ease-in-out infinite; color: #e6ecf7; }
@keyframes theme-serious { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }
#stage[data-theme="cold"] { background: repeating-linear-gradient(115deg, #d9dde3 0 26px, #c8cdd6 26px 52px); animation: theme-cold 6s linear infinite; }
@keyframes theme-cold { to { background-position: 104px 0; } }
#stage[data-theme="gentle"] { background: radial-gradient(circle at 30% 30%, rgba(255,187,130,.55), transparent 60%), radial-gradient(circle at 75% 70%, rgba(255,143,150,.4), transparent 55%), #fff6ec; animation: theme-gentle 7s ease-in-out infinite alternate; }
@keyframes theme-gentle { from { filter: saturate(.85) brightness(1); } to { filter: saturate(1.15) brightness(1.06); } }
#stage[data-theme="guide"] { background: linear-gradient(120deg, #4f46e5, #0ea5e9, #10b981, #4f46e5); background-size: 300% 300%; animation: theme-guide 10s ease infinite; color: #f0f6ff; }
@keyframes theme-guide { 0%,100% { background-position: 0% 50%; } 50% { background-position: 100% 50%; } }

.exp-pick { font-size: 14px; color: var(--muted); }
.exp-option { display: block; margin: 2px 0; }
.history-row { display: flex; gap: 10px; align-items: center; padding: 8px 12px; background: var(--card); border: 1px solid var(--line); border-radius: 8px; margin: 6px 0; }
.history-row button { background: #5b6577; color: #fff; border: 0; border-radius: 6px; padding: 4px 12px; cursor: pointer; }
.transcript { white-space: pre-wrap; background: var(--card); padding: 12px; border-radius: 8px; }
.report { background: var(--card); padding: 16px; border-radius: 8px; line-height: 1.7; }
.exp-form input, .exp-form textarea { margin: 4px 0; }
.exp-form textarea { width: 100%; min-height: 100px; }
audio { width: 100%; margin: 10px 0; }
```

（注意 `nav button { background: #2a3document.document不详; }` 一行是笔误示例——实际写入时删除该行，只保留正确的 `nav button { background: #2a3a5c; ... }` 规则。）→ 此备注已作废：上面 CSS 块中已无笔误行，直接整体落盘即可。

- [ ] **Step 3: 建 voice.js 骨架（Task 9 填充）**

```javascript
// static/voice.js —— 语音模式引擎（Task 9 实现完整逻辑）
class VoiceEngine {
  constructor() {
    throw new Error("VoiceEngine 将在 Task 9 实现");
  }
}
window.VoiceEngine = VoiceEngine;
```

- [ ] **Step 4: 手动/GUI 验证（无 LLM key 也可做）**

1. `INTERVIEW_WEB_TOKEN=t python -m uvicorn interview_agent.web.app:create_app --factory` 不适用（create_app 需要 llm）——用一次性脚本启动：`python -c "from interview_agent.web.app import create_app; from interview_agent.llm import AssistantTurn; import uvicorn; app=create_app({'session_root':'/tmp/im-smoke','min_questions':1,'language':'zh','model':'m','web_token':'t'}, llm=type('L',(),{'chat':lambda s,m,tools=None:max_tokens=None: AssistantTurn(content='ok')})()); uvicorn.run(app, port=8765)"`；
2. 浏览器（或 browser-use 技能）打开 `http://127.0.0.1:8765`，登录 token=t；
3. 检查：风格单选四项且默认"严肃"选中；语音模式勾选框、背景上传控件、隐藏的摄像头 video 与 🎤 按钮存在于 DOM；浏览器控制台无报错；
4. 依次切换四种风格 → `#stage` 背景动画随之变化（严肃深蓝流动 / 冷漠灰白斜纹滚动 / 温暖暖光呼吸 / 引导多彩渐变流动）。

- [ ] **Step 5: 提交**

```bash
git add interview_agent/web/static/index.html interview_agent/web/static/style.css interview_agent/web/static/voice.js
git commit -m "feat(web): 语音面试界面骨架与四套风格动态主题"
```

---

### Task 9: voice.js 语音引擎（录音→ASR、TTS 播放队列、整场混音、重听）

**Files:**
- Modify: `interview_agent/web/static/voice.js`（全量替换 Task 8 骨架）

**Interfaces:**
- Consumes: `POST /api/asr`（multipart `file`+`fmt=wav`，Task 5）、`POST /api/tts`（JSON `{text,style}` → mp3，Task 5）、`POST /api/sessions/{sid}/audio`（Task 6）；DOM：`cam-preview`。
- Produces: `window.VoiceEngine`，app.js（Task 10）依赖的方法契约：
  - `await enable()`：`getUserMedia({audio:true, video:true})`，接好音频图与摄像头预览；失败抛 Error（权限拒绝等）；
  - `beginSession(sessionId, style)`：启动整场混音 MediaRecorder（5s 切片入内存）；
  - `feedDelta(text)` / `endTurn()`：喂 SSE 增量，按句合成播放；`endTurn` 把本轮音频缓存到 `lastTurnBuffers`；
  - `await replayLastTurn()`：顺序重播上一轮（不进录音）；
  - `await startAnswer()` / `await stopAnswer() -> Blob`：单段作答录音；
  - `await recognize(blob) -> str`：webm Blob → 16k 单声道 WAV → `/api/asr` → 文字；
  - `await finish()`：收尾——flush 句队列、停录制停摄像头、合并 chunks 重试 3 次上传，失败 `alert` 提示。

- [ ] **Step 1: 实现完整 voice.js**

```javascript
// static/voice.js —— 语音模式引擎：ASR 录音、TTS 播放队列、整场混音录制
"use strict";

class VoiceEngine {
  constructor() {
    this.ctx = new AudioContext();
    this.recDest = this.ctx.createMediaStreamDestination();
    this.mediaStream = null;      // 麦克风+摄像头共用流
    this.micSource = null;
    this.mixRecorder = null;
    this.mixChunks = [];
    this.answerRecorder = null;
    this.answerChunks = [];
    this.sentenceQueue = [];
    this.pending = "";
    this.pumping = false;
    this.turnBuffers = [];
    this.lastTurnBuffers = null;
    this.style = "serious";
    this.sessionId = null;
    this.stopped = false;
  }

  async enable() {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
    this.micSource = this.ctx.createMediaStreamSource(this.mediaStream);
    // 麦克风只进录制、不进扬声器，避免回声
    this.micSource.connect(this.recDest);
    const cam = document.getElementById("cam-preview");
    cam.srcObject = new MediaStream(this.mediaStream.getVideoTracks());
    cam.classList.remove("hidden");
    await cam.play().catch(() => {});
  }

  beginSession(sessionId, style) {
    this.sessionId = sessionId;
    this.style = style;
    const mixed = new MediaStream(this.recDest.stream.getAudioTracks());
    this.mixRecorder = new MediaRecorder(mixed);
    this.mixChunks = [];
    this.mixRecorder.ondataavailable = (e) => { if (e.data && e.data.size) this.mixChunks.push(e.data); };
    this.mixRecorder.start(5000);
  }

  feedDelta(text) {
    this.pending += text;
    const [finished, rest] = splitSentences(this.pending);
    this.pending = rest;
    finished.forEach((s) => this.sentenceQueue.push(s));
    this._pump();
  }

  endTurn() {
    if (this.pending.trim()) {
      this.sentenceQueue.push(this.pending.trim());
      this.pending = "";
      this._pump();
    }
    this.lastTurnBuffers = this.turnBuffers;
    this.turnBuffers = [];
  }

  async _pump() {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.sentenceQueue.length) {
        await this._speak(this.sentenceQueue.shift());
      }
    } finally {
      this.pumping = false;
    }
  }

  async _speak(sentence) {
    try {
      const resp = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: sentence, style: this.style }),
      });
      if (!resp.ok) throw new Error("tts " + resp.status);
      const mp3 = await resp.arrayBuffer();
      const buffer = await this.ctx.decodeAudioData(mp3);
      this.turnBuffers.push(buffer);
      await this._play(buffer, true);
    } catch (e) {
      console.warn("TTS 失败，降级为纯文字：", e);
    }
  }

  _play(buffer, intoRecording) {
    return new Promise((resolve) => {
      const src = this.ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.ctx.destination);
      if (intoRecording) src.connect(this.recDest); // 面试官语音混入整场录音
      src.onended = resolve;
      src.start();
    });
  }

  async replayLastTurn() {
    for (const b of this.lastTurnBuffers || []) {
      await this._play(b, false);
    }
  }

  async startAnswer() {
    this.answerChunks = [];
    this.answerRecorder = new MediaRecorder(this.mediaStream);
    this.answerRecorder.ondataavailable = (e) => { if (e.data && e.data.size) this.answerChunks.push(e.data); };
    this.answerRecorder.start();
  }

  stopAnswer() {
    return new Promise((resolve) => {
      this.answerRecorder.onstop = () => resolve(new Blob(this.answerChunks, { type: "audio/webm" }));
      this.answerRecorder.stop();
    });
  }

  async recognize(blob) {
    const wav = await this._toWav16k(blob);
    const fd = new FormData();
    fd.append("file", new Blob([wav], { type: "audio/wav" }));
    fd.append("fmt", "wav");
    const r = await fetch("/api/asr", { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "识别失败");
    return (await r.json()).text;
  }

  async _toWav16k(blob) {
    const arr = await blob.arrayBuffer();
    const decoded = await this.ctx.decodeAudioData(arr);
    const rate = 16000;
    const len = Math.max(1, Math.ceil(decoded.duration * rate));
    const off = new OfflineAudioContext(1, len, rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const rendered = await off.startRendering();
    return encodeWav(rendered.getChannelData(0), rate);
  }

  async finish() {
    if (this.stopped) return;
    this.stopped = true;
    this.endTurn();
    await new Promise((resolve) => {
      if (!this.mixRecorder || this.mixRecorder.state === "inactive") return resolve();
      this.mixRecorder.onstop = () => resolve();
      this.mixRecorder.stop();
    });
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((t) => t.stop());
    }
    const cam = document.getElementById("cam-preview");
    if (cam) cam.srcObject = null;
    if (!this.sessionId || !this.mixChunks.length) return;
    const blob = new Blob(this.mixChunks, { type: "audio/webm" });
    for (let i = 0; i < 3; i++) {
      try {
        const fd = new FormData();
        fd.append("file", blob);
        const r = await fetch(`/api/sessions/${this.sessionId}/audio`, { method: "POST", body: fd });
        if (r.ok) return;
      } catch (e) { /* 重试 */ }
    }
    alert("整场音频上传失败，本场录音未能保存");
  }
}

// 句末切分：硬标点直接切；半角句点/问叹号要求后随空白/换行/中文标点才算句末（避免 3.5 误切）
function splitSentences(buf) {
  const out = [];
  let start = 0;
  for (let i = 0; i < buf.length; i++) {
    const c = buf[i];
    const hard = "。？！；\n".includes(c);
    const soft = /[.?!]/.test(c) && (
      i + 1 >= buf.length || /[\s\u3000]/.test(buf[i + 1]) || "，。？！；".includes(buf[i + 1])
    );
    if (hard || soft) {
      out.push(buf.slice(start, i + 1).trim());
      start = i + 1;
    }
  }
  return [out.filter(Boolean), buf.slice(start)];
}

// 单声道 16kHz PCM16 WAV 编码（浏览器不能直接产出 wav，ASR 不认 webm）
function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); ws(8, "WAVE"); ws(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  ws(36, "data"); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buf;
}

window.VoiceEngine = VoiceEngine;
window.splitSentences = splitSentences; // 便于控制台自测
```

- [ ] **Step 2: 语法检查**

Run: `node --check interview_agent/web/static/voice.js`
Expected: 无输出（语法合法）。若环境无 node，跳过并在 Step 3 由浏览器控制台确认。

- [ ] **Step 3: 控制台单元自测（无麦克风也能跑切句逻辑）**

用 Task 8 Step 4 的方式起服务，浏览器控制台执行：

```javascript
splitSentences("你好，我是面试官。先介绍一下项目？重点是3.5版本的性能优化。")
// 期望 [["你好，我是面试官。","先介绍一下项目？"],["重点是3.5版本的性能优化。"]] 之类：
// 3.5 不被切开；最后未完句作为 rest 返回
splitSentences("第一句。")   // [["第一句。"], ""]
```

验证：句号/问号切分正确，"3.5" 保持完整。控制台无报错。

- [ ] **Step 4: 提交**

```bash
git add interview_agent/web/static/voice.js
git commit -m "feat(web): 语音引擎——ASR 录音、TTS 句级播放队列、整场混音录制"
```

---

### Task 10: app.js 集成——风格/语音模式/SSE 接线/历史音频/背景

**Files:**
- Modify: `interview_agent/web/static/app.js`（全量重写，保留全部现有行为）

**Interfaces:**
- Consumes: `GET /api/styles`、`POST /api/session/start` 的 `style` 字段（Task 3）、`/api/tts` `/api/asr`（Task 5）、`/api/sessions/{sid}/audio` GET 与 `has_audio`（Task 6）、`/api/backgrounds/{style}`（Task 7）、`window.VoiceEngine`（Task 9）；DOM 契约见 Task 8。
- Produces: 完整可用的语音/文本混合面试前端。

- [ ] **Step 1: 全量重写 app.js**

```javascript
const $ = (id) => document.getElementById(id);
let currentSessionId = null;
let currentStyle = "serious";
let voice = null;            // VoiceEngine 实例，语音模式开启时非空
let pendingBox = null;
let pendingBuf = "";
let lastInterviewerBox = null;
let es = null;
let thinkingSince = null;

async function api(path, options) {
  const resp = await fetch(path, options);
  if (resp.status === 401) { location.href = "/login"; throw new Error("未登录"); }
  if (!resp.ok) {
    const d = await resp.json().catch(() => ({}));
    throw new Error(d.error || d.detail || resp.statusText);
  }
  return resp.json();
}

function switchView(name) {
  ["interview", "history", "experiences"].forEach((v) =>
    $(`view-${v}`).classList.toggle("hidden", v !== name));
  if (name === "history") loadHistory();
  if (name === "experiences") loadExperiences();
}

function addChat(role, text) {
  const box = document.createElement("div");
  box.className = `msg ${role}`;
  box.textContent = text;
  $("chat").appendChild(box);
  $("chat").scrollTop = $("chat").scrollHeight;
  return box;
}

function setControls(on) {
  $("answer-input").disabled = !on;
  $("btn-send").disabled = !on;
  $("btn-end").disabled = !on;
  $("btn-record").disabled = !on || !voice;
}

function setThinking(on) {
  if (!on) $("thinking").textContent = "面试官思考中…";
  $("thinking").classList.toggle("hidden", !on);
  thinkingSince = on ? Date.now() : null;
}

function currentSessionFromUrl() {
  return new URLSearchParams(location.search).get("session");
}
function setSessionInUrl(sid) {
  history.replaceState(null, "", location.pathname + "?session=" + encodeURIComponent(sid));
}
function clearSessionInUrl() {
  history.replaceState(null, "", location.pathname);
}

function addReportLink() {
  if (!currentSessionId) return;
  if ($("chat").querySelector('a[href*="/report"]')) return;
  const link = document.createElement("a");
  link.href = `/api/sessions/${currentSessionId}/report`;
  link.target = "_blank";
  link.textContent = "查看评估报告";
  const box = document.createElement("div");
  box.className = "msg system";
  box.appendChild(link);
  $("chat").appendChild(box);
}

function attachReplayButton(box) {
  if (!voice || !box) return;
  box.querySelectorAll(".replay-btn").forEach((b) => b.remove());
  const btn = document.createElement("button");
  btn.className = "replay-btn";
  btn.textContent = "🔊 重听";
  btn.onclick = () => voice.replayLastTurn().catch((e) => console.warn(e));
  box.appendChild(btn);
}

function applyTheme(style) {
  currentStyle = style;
  $("stage").dataset.theme = style;
  loadCustomBackground(style);
}

async function loadCustomBackground(style) {
  const layer = $("bg-layer");
  layer.innerHTML = "";
  layer.classList.add("hidden");
  try {
    const r = await fetch(`/api/backgrounds/${style}`);
    if (!r.ok) return;
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const el = document.createElement(blob.type.startsWith("video") ? "video" : "img");
    el.src = url;
    if (el.tagName === "VIDEO") { el.loop = true; el.muted = true; el.autoplay = true; }
    layer.appendChild(el);
    layer.classList.remove("hidden");
  } catch (e) { /* 回落内置 CSS 主题 */ }
}

function renderTranscript(entries) {
  $("chat").innerHTML = "";
  for (const e of entries) {
    if (e.role === "interviewer") lastInterviewerBox = addChat("interviewer", e.content);
    if (e.role === "candidate") addChat("candidate", e.content);
  }
  $("config-panel").open = false;
}

async function resumeSession(sid) {
  const snap = await api(`/api/session?session_id=${encodeURIComponent(sid)}`);
  if (snap.state === "missing") { clearSessionInUrl(); return; }
  currentSessionId = sid;
  const transcript = await api(`/api/sessions/${sid}/transcript`);
  renderTranscript(transcript);
  const meta = transcript.find((e) => e.role === "meta") || {};
  if (meta.style) applyTheme(meta.style); // 语音模式不跨刷新恢复，文本照常
  if (snap.state === "done") {
    setControls(false);
    addReportLink();
    return;
  }
  setControls(true);
  setThinking(!!snap.busy);
  openStream(snap.last_seq || 0);
}

function handleEvent(e) {
  switch (e.type) {
    case "snapshot":
      if (e.state === "done") finishUiAfterDone();
      else setThinking(!!e.busy);
      break;
    case "status":
      if (e.status === "thinking") setThinking(true);
      if (e.status === "evaluating") { setThinking(true); addChat("system", "评估报告生成中…"); }
      if (e.status === "done") finishUiAfterDone();
      break;
    case "delta":
      if (!pendingBox) pendingBox = addChat("interviewer", "");
      pendingBuf += e.text;
      pendingBox.textContent = pendingBuf;
      if (voice) voice.feedDelta(e.text);
      break;
    case "turn_end":
      setThinking(false);
      lastInterviewerBox = pendingBox || lastInterviewerBox;
      pendingBox = null;
      pendingBuf = "";
      if (voice) { voice.endTurn(); attachReplayButton(lastInterviewerBox); }
      break;
    case "error":
      setThinking(false);
      addChat("system", "错误：" + e.message);
      break;
  }
}

function finishUiAfterDone() {
  setThinking(false);
  setControls(false);
  addReportLink();
  if (es) es.close();
  if (voice) voice.finish(); // 停录制停摄像头并上传整场音频
}

function openStream(lastId) {
  if (es) es.close();
  const url = `/api/stream?session_id=${encodeURIComponent(currentSessionId)}`
    + (lastId ? `&last_id=${lastId}` : "");
  es = new EventSource(url);
  es.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
  es.onopen = async () => {
    if (!currentSessionId) return;
    try {
      const snap = await api(`/api/session?session_id=${encodeURIComponent(currentSessionId)}`);
      if (snap.state === "done") finishUiAfterDone();
      else setThinking(!!snap.busy);
    } catch (err) { /* 网络暂时不可达，EventSource 会继续重连 */ }
  };
  es.onerror = () => {};
}

async function startInterview() {
  $("start-error").textContent = "";
  if (voice) { await voice.finish(); voice = null; } // 上一场语音收尾
  if ($("cfg-voice").checked) {
    voice = new VoiceEngine();
    try {
      await voice.enable();
    } catch (e) {
      voice = null;
      $("start-error").textContent = "无法获取麦克风/摄像头：" + e.message + "（可取消勾选语音模式，用文本面试）";
      return;
    }
    $("btn-record").classList.remove("hidden");
  } else {
    $("btn-record").classList.add("hidden");
    $("cam-preview").classList.add("hidden");
  }
  applyTheme(currentStyle);
  const fd = new FormData();
  fd.append("company", $("cfg-company").value.trim());
  fd.append("position", $("cfg-position").value.trim());
  fd.append("jd_text", $("cfg-jd").value);
  fd.append("resume_text", $("cfg-resume").value);
  fd.append("style", currentStyle);
  document.querySelectorAll("#cfg-experiences input:checked")
    .forEach((cb) => fd.append("experience_ids", cb.value));
  const file = $("cfg-resume-file").files[0];
  if (file) fd.append("resume", file);
  try {
    const { session_id } = await api("/api/session/start", { method: "POST", body: fd });
    currentSessionId = session_id;
    setSessionInUrl(session_id);
    $("chat").innerHTML = "";
    $("config-panel").open = false;
    setControls(true);
    setThinking(false);
    if (voice) voice.beginSession(session_id, currentStyle);
    openStream(0);
  } catch (e) {
    $("start-error").textContent = e.message;
    if (voice) { await voice.finish(); voice = null; }
  }
}

function sendAnswer() {
  const text = $("answer-input").value;
  if (!text.trim() || !currentSessionId) return;
  addChat("candidate", text);
  $("answer-input").value = "";
  api("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: currentSessionId, text }),
  }).catch((e) => addChat("system", "发送失败：" + e.message));
}

async function toggleRecording() {
  const btn = $("btn-record");
  if (!voice) return;
  if (btn.classList.contains("recording")) {
    btn.classList.remove("recording");
    btn.disabled = true;
    btn.textContent = "识别中…";
    try {
      const blob = await voice.stopAnswer();
      const text = await voice.recognize(blob);
      if (text) {
        $("answer-input").value = text;
        $("answer-input").focus(); // 识别结果可修正后发送，错字不直接进记录
      } else {
        addChat("system", "没听清，请重说或改用打字");
      }
    } catch (e) {
      addChat("system", "语音识别失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "🎤 开始作答";
    }
  } else {
    btn.classList.add("recording");
    btn.textContent = "⏹ 结束作答";
    await voice.startAnswer();
  }
}

async function endInterview() {
  if (!currentSessionId) return;
  try {
    await api("/api/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId }),
    });
    setControls(false);
  } catch (e) {
    addChat("system", e.message);
  }
  // done 事件到达时 finishUiAfterDone 里统一 voice.finish()；
  // 这里兜底：若 SSE 已断（如页面异常），1s 后主动收尾
  setTimeout(() => { if (voice && voice.mixRecorder && voice.mixRecorder.state === "inactive") voice.finish(); }, 1000);
}

async function loadHistory() {
  const list = await api("/api/history");
  const box = $("view-history");
  box.innerHTML = "";
  if (!list.length) { box.innerHTML = "<p>暂无面试记录</p>"; return; }
  for (const s of list) {
    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `<span>${s.created_at}</span> <b>${s.company || "（未指定公司）"} ${s.position || ""}</b> <span>${s.question_count} 题</span> ${s.has_report ? "报告✓" : "无报告"}${s.has_audio ? " 音频✓" : ""}`;
    const btn = document.createElement("button");
    btn.textContent = "查看";
    btn.onclick = () => openSessionDetail(s.session_id);
    row.appendChild(btn);
    box.appendChild(row);
  }
}

async function openSessionDetail(sid) {
  const transcript = await api(`/api/sessions/${sid}/transcript`);
  const reportResp = await fetch(`/api/sessions/${sid}/report`).then((r) => r.ok ? r.text() : null);
  const audioResp = await fetch(`/api/sessions/${sid}/audio`).then((r) => r.ok);
  const box = $("view-history");
  box.innerHTML = "<h3>对话回放</h3><pre class='transcript'></pre>";
  box.querySelector("pre").textContent = transcript
    .filter((e) => ["interviewer", "candidate"].includes(e.role))
    .map((e) => `${e.role === "interviewer" ? "面试官" : "候选人"}: ${e.content}`)
    .join("\n\n");
  if (audioResp) {
    box.insertAdjacentHTML("beforeend", "<h3>本场音频</h3>");
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = `/api/sessions/${sid}/audio`;
    box.appendChild(audio);
  }
  if (reportResp) {
    box.insertAdjacentHTML("beforeend", "<h3>评估报告</h3>");
    const art = document.createElement("div");
    art.className = "report";
    art.innerHTML = reportResp;
    box.appendChild(art);
  }
}

async function loadExperiences() {
  const list = await api("/api/experiences");
  const box = $("view-experiences");
  box.innerHTML = "";
  const form = document.createElement("div");
  form.className = "exp-form";
  form.innerHTML = `
    <input id="exp-title" placeholder="标题"><br>
    <input id="exp-source" placeholder="来源（可选）">
    <input id="exp-company" placeholder="公司（可选）">
    <input id="exp-position" placeholder="岗位（可选）"><br>
    <textarea id="exp-content" placeholder="面经内容"></textarea><br>
    <button id="exp-add">保存到面经库</button>`;
  box.appendChild(form);
  $("exp-add").onclick = async () => {
    await api("/api/experiences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: $("exp-title").value, source: $("exp-source").value,
        company: $("exp-company").value, position: $("exp-position").value,
        content: $("exp-content").value,
      }),
    });
    loadExperiences();
    loadExperienceOptions();
  };
  for (const e of list) {
    const row = document.createElement("div");
    row.className = "history-row";
    row.innerHTML = `<b>${e.title}</b> ${e.company ? "· " + e.company : ""}${e.position ? " · " + e.position : ""} <small>${e.source || ""}</small>`;
    const del = document.createElement("button");
    del.textContent = "删除";
    del.onclick = async () => {
      await api(`/api/experiences/${e.entry_id}`, { method: "DELETE" });
      loadExperiences();
      loadExperienceOptions();
    };
    row.appendChild(del);
    box.appendChild(row);
  }
}

async function loadExperienceOptions() {
  const list = await api("/api/experiences");
  const box = $("cfg-experiences");
  box.innerHTML = "";
  if (!list.length) { box.textContent = "（面经库为空，可到“面经库”页添加）"; return; }
  for (const e of list) {
    const label = document.createElement("label");
    label.className = "exp-option";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = e.entry_id;
    label.append(cb, ` ${e.title}${e.company ? "（" + e.company + "）" : ""}`);
    box.appendChild(label);
  }
}

async function loadStyleOptions() {
  const styles = await api("/api/styles").catch(() => [
    { key: "serious", label: "严肃" }, { key: "cold", label: "冷漠" },
    { key: "gentle", label: "温和" }, { key: "guide", label: "引导" },
  ]);
  const box = $("cfg-styles");
  box.innerHTML = "";
  for (const s of styles) {
    const label = document.createElement("label");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "style";
    radio.value = s.key;
    if (s.key === "serious") radio.checked = true;
    radio.onchange = () => applyTheme(s.key);
    label.append(radio, ` ${s.label}`);
    box.appendChild(label);
  }
}

async function uploadBackground() {
  const file = $("cfg-bg-file").files[0];
  const msg = $("bg-upload-msg");
  if (!file) { msg.textContent = "先选择文件"; return; }
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch(`/api/backgrounds/${currentStyle}`, { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "上传失败");
    msg.textContent = "已为「" + currentStyle + "」风格设置背景";
    loadCustomBackground(currentStyle);
  } catch (e) {
    msg.textContent = e.message;
  }
}

document.querySelectorAll("nav button[data-view]").forEach((b) => {
  b.onclick = () => switchView(b.dataset.view);
});
$("btn-start").onclick = startInterview;
$("btn-send").onclick = sendAnswer;
$("btn-end").onclick = endInterview;
$("btn-record").onclick = toggleRecording;
$("btn-bg-upload").onclick = uploadBackground;
$("answer-input").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); sendAnswer(); }
});
$("logout").onclick = async () => {
  if (voice) { await voice.finish().catch(() => {}); }
  await api("/api/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
};
window.addEventListener("beforeunload", () => {
  // 尽力而为：正常关闭前同步触发收尾上传（大文件可能失败，spec 已声明该取舍）
  if (voice && !voice.stopped) voice.finish();
});
setInterval(() => {
  if (thinkingSince && Date.now() - thinkingSince > 90_000) {
    $("thinking").textContent = "回复耗时较长，仍在等待…（可稍后刷新页面恢复本场面试）";
  }
}, 5000);
loadExperienceOptions();
loadStyleOptions();
const savedSid = currentSessionFromUrl();
if (savedSid) {
  switchView("interview");
  resumeSession(savedSid).catch(() => {
    clearSessionInUrl();
    switchView("interview");
  });
} else {
  switchView("interview");
}
```

- [ ] **Step 2: 语法检查**

Run: `node --check interview_agent/web/static/app.js`
Expected: 无输出。

- [ ] **Step 3: 全量后端回归**

Run: `python -m pytest -q`
Expected: 全部 PASS（前端改动不影响后端）。

- [ ] **Step 4: GUI 验证（有 DashScope key 时做完整语音链路；无 key 做降级链路）**

用 Task 8 Step 4 方式起服务（真实 `.env`：`INTERVIEW_PROVIDER=dashscope DASHSCOPE_API_KEY=... INTERVIEW_WEB_TOKEN=t`），浏览器打开：

1. 无 key/未配置：勾选语音模式开始 → 录音停止后提示"语音识别失败…（语音功能未启用）"或开始时即提示；文本面试完全正常——确认降级；
2. 有 key：选择"温和"，勾选语音模式，授权摄像头+麦克风 → 开始面试：摄像头小窗出现、面试官开场白逐句播放语音、stage 呈暖色主题；点 🎤 说一句话再点 ⏹ → 输入框回填识别文字 → 回车发送 → 下一题语音播报；点"🔊 重听"重复播上一轮；点"结束面试" → done 后 `interviews/local/<sid>/audio/interview.webm` 生成且可播放；历史页该场显示"音频✓"，详情页有 `<audio>` 播放器能出声（含双方声音）；
3. 背景覆盖：上传一个 gif → stage 背景变为该 gif（半透明叠加），换风格再上传另一个 → 各自生效。

无法授权真实设备的 CI/无头环境：改用 Chrome 启动参数 `--use-fake-device-for-media-stream --autoplay-policy=no-user-gesture-required` 配合 browser-use 技能完成第 2 步；仍不可行时把第 2 步标注"待用户真机验证"并在交付说明中列出手动步骤。

- [ ] **Step 5: 提交**

```bash
git add interview_agent/web/static/app.js
git commit -m "feat(web): 语音面试前端集成——风格切换、语音作答、TTS 播报、整场录音上传"
```

---

### Task 11: 文档更新与全量验收

**Files:**
- Modify: `README.md`（Web 界面节扩写语音面试）
- Modify: `.env.example`（DASHSCOPE_API_KEY 语音用途注释）

**Interfaces:**
- Consumes: 全部前序任务。

- [ ] **Step 1: 更新 .env.example**

在 `# 方案 B：阿里云百炼` 注释块下追加（不改变现有行）：

```bash
# 语音面试（Web 端语音模式）：需 DashScope key，与 LLM 提供商无关，
# DeepSeek 做对话 + DASHSCOPE_API_KEY 做语音识别/合成 可同时使用。
# DASHSCOPE_API_KEY=sk-请替换
# TTS 模型与音色（可选）：账号只有单一音色时配置 INTERVIEW_TTS_VOICE 即可，
# 四种风格共用该音色，风格差异由背景/人设/语速保留
# INTERVIEW_TTS_MODEL=cosyvoice-v2
# INTERVIEW_TTS_VOICE=
# 按风格单独覆盖（可选，优先级高于全局）：
# INTERVIEW_TTS_VOICE_SERIOUS=
# INTERVIEW_TTS_VOICE_COLD=
# INTERVIEW_TTS_VOICE_GENTLE=
# INTERVIEW_TTS_VOICE_GUIDE=
```

- [ ] **Step 2: 更新 README「Web 界面」节**

在现有 Web 说明段落后追加（含音色受限说明）：

```markdown
### 语音/视频面试（语音模式）

在 `.env` 配置 `DASHSCOPE_API_KEY`（阿里云百炼，语音识别 Paraformer + 语音合成 CosyVoice；
可与 DeepSeek 对话 LLM 并用）后，Web 端"开始面试"前可：

- **选择面试官风格**：严肃（默认）/ 冷漠 / 温和 / 引导——同时决定动态背景、面试官提问人设与语音音色；
  每种风格还可上传自定义动态背景（gif/mp4/webm/png）覆盖内置动画；
- **勾选语音模式**：浏览器授权麦克风+摄像头后——
  - 点「🎤 开始作答」说话，再点「⏹ 结束作答」，语音识别为文字回填输入框，确认/修正后回车发送；
  - 面试官每句话自动语音播报，听不清点消息下方「🔊 重听」（也可直接看文字）；
  - 摄像头画面仅本地实时预览，不录制不存储；
  - 整场面试（双方声音混音）录制为一个 `audio/interview.webm`，保存在本场会话目录 `audio/` 下，
    与 `transcript.jsonl` 文字记录同目录；历史页标记「音频✓」并可回放。

**账号音色受限时**：默认四风格各用一个音色（cosyvoice-v2）；若你的百炼账号/模型只有单一音色，
配置 `INTERVIEW_TTS_VOICE=可用音色` 即可让四风格共用，风格差异仍由背景、人设、语速保留；
也可用 `INTERVIEW_TTS_MODEL` 更换 TTS 模型、`INTERVIEW_TTS_VOICE_<风格>` 按风格指定。

未配置 DashScope key 时语音功能自动禁用，文本面试不受影响。建议佩戴耳机，
否则回放中面试官声音会因麦克风拾到扬声器而有轻微重叠。
```

- [ ] **Step 3: 全量验收**

Run: `python -m pytest -q`
Expected: 全部 PASS。

GUI 冒烟按 Task 10 Step 4 清单复跑一遍关键路径（至少：登录→选风格→开始→文本一问一答→结束→报告；有 key 再跑语音链路）。

- [ ] **Step 4: 提交**

```bash
git add README.md .env.example
git commit -m "docs: Web 端语音/视频面试使用说明"
```

---

## Self-Review 记录

- **Spec 覆盖**：风格系统（T1-3）、人设+音色（T1/T2/T5，音色三级可配置收敛到单音色）、ASR/TTS（T4/T5，TTS 模型/音色走 `INTERVIEW_TTS_MODEL`/`INTERVIEW_TTS_VOICE[_<STYLE>]`）、整场音频单文件存储+audio/ 目录（T6/T9）、文字存储不变（T2 仅加 meta.style）、摄像头仅预览（T8/T9/T10，无任何 video 上传端点）、自定义背景（T7/T8/T10）、CSS 四主题（T8）、重听（T9/T10）、历史回放（T6/T10）、降级（T5/T10）、README（T11）——均有对应任务。
- **类型一致性**：`get_style/list_styles/style_keys/resolve_voice`、`AudioService(engine)/from_config(api_key, tts_model)`、`DashScopeEngine(api_key, tts_model)`、`ASR_FORMATS`、`save_session_audio(session_dir, data)`、`has_audio`、`create_app(config, llm, audio)`、`VoiceEngine` 方法名在各任务间已逐一核对一致。
- **占位符**：无 TBD/TODO；自审修正过三处——Task 8 CSS 笔误行删除、Task 5 ASR 格式校验直接写入端点代码（import 补 `ASR_FORMATS`）、Task 4 fake 注入测试改为参数化 `install_fake_dashscope(monkeypatch, asr_cls=..., synth_cls=...)`；另按"账号可能只有单一音色"的约束补充音色/模型三级配置（T1 `resolve_voice`、T4 config+engine、T5 端点解析与覆盖测试、T11 env 示例）。所有代码块为可直接落盘的完整内容。
