# Web 端语音/视频面试 设计文档

日期：2026-08-26
状态：已与用户确认关键决策，待实现

## 背景与目标

现有 Web 端（FastAPI + SSE）只支持文本面试。本设计为其拓展语音/视频面试能力：

1. 面试官背景为指定动态效果，可按"严肃 / 冷漠 / 温和 / 引导"四种风格切换；
2. 面试过程用户可语音输入（ASR），面试官语音输出（TTS）；
3. 语音文件落盘存储：**一场面试一个完整音频文件**（不按段落拆分），存于会话目录 `audio/` 下；
4. 文字记录维持现状（`transcript.jsonl`），与音频同目录存放；
5. 面试过程启用摄像头，**仅实时预览，不录制不存储**。

## 已确认的决策

| 决策点 | 选择 |
|---|---|
| ASR/TTS 供应商 | 阿里云百炼 DashScope（Paraformer 录音文件识别 + CosyVoice 合成），与现有 DashScope LLM provider 共用 `DASHSCOPE_API_KEY` |
| 背景素材 | 内置 CSS 动画主题为默认 + 支持按风格上传自定义 GIF/视频覆盖 |
| 摄像头 | 仅实时预览，不录制 |
| 风格作用 | 同时影响：动态背景、注入系统提示词的面试官人设、TTS 音色/语速 |
| 音频存储 | 浏览器整场混音连续录制，结束时上传一个完整文件到 `audio/`；ASR/TTS 中间数据不落档 |
| 文字存储 | 维持 `transcript.jsonl` 不变 |

## 总体架构（方案 A：语音作为现有会话的"语音模式"）

复用现有 `InterviewTask` 会话循环、SSE 事件流与存储结构，不新建会话类型。语音模式与文本模式可在同一场面试中混用。新增三个后端模块与若干端点，前端在现有页面内扩展。

```
浏览器                                   服务端
──────                                   ──────
麦克风 ──┐
TTS 播放 ─┴─ WebAudio 混音 ─ MediaRecorder（整场连续）
              │                        ┌ /api/asr        临时上传→识别→删（单段）
              │                        ├ /api/tts        文本+风格→合成音频流
              └ 结束时一次上传 ────────► └ /api/sessions/{sid}/audio
                                           → 存 interviews/<uid>/<sid>/audio/interview.webm
摄像头 ── <video> 实时预览（不录制）
SSE delta ── 句末切分 ── /api/tts ── AudioContext 同时→扬声器 + 混音录制
```

## 模块设计

### 1. `interview_agent/web/persona.py` — 风格系统

```python
@dataclass(frozen=True)
class PersonaStyle:
    key: str            # serious / cold / gentle / guide
    label: str          # 严肃 / 冷漠 / 温和 / 引导
    prompt_fragment: str  # 追加到系统提示词的人设描述
    tts_voice: str      # CosyVoice 音色名
    tts_speed: float    # 语速

def get_style(key: str) -> PersonaStyle  # 未知 key 回落默认（温和）
def list_styles() -> list[PersonaStyle]
```

四种风格的人设方向：严肃=追问犀利、节奏紧凑；冷漠=简短克制、不鼓励不寒暄；温和=友好包容、多给肯定；引导=循循诱导、给提示拆解问题。

音色初值：严肃=`longcheng`（沉稳男声）、冷漠=`longshu`（冷静）、温和=`longwan`（亲和女声）、引导=`longxiaochun`（明亮）；实现阶段在沙箱 key 上验证音色名可用性，不可用则替换为同风格取向的可用音色并在代码注释中记录。

### 2. `interview_agent/web/audio.py` — DashScope 语音服务

```python
class AudioService:
    def __init__(self, api_key: str, client=None)   # client 可注入 fake 供测试
    def transcribe(self, audio_bytes: bytes, fmt: str) -> str   # Paraformer 录音文件识别
    def synthesize(self, text: str, voice: str, speed: float) -> bytes  # CosyVoice 合成 mp3
```

- 识别走"本地文件 bytes → SDK 接口"，不经公网临时 URL；合成返回完整 mp3 字节。
- 未配置 `DASHSCOPE_API_KEY` 时 `AudioService` 不可用（`available=False`），端点返回明确错误，前端置灰语音按钮。
- 依赖：`dashscope` SDK 进主依赖（`pyproject.toml` dependencies），保持一条安装路径。

### 3. 新增 HTTP 端点（`web/app.py`）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/asr` | POST | multipart 音频（webm/ogg/wav/mp3，≤20MB）→ 临时落盘 `.uploads/` → 识别 → 删除临时文件 → `{"text": "..."}` |
| `/api/tts` | POST | JSON `{text, style}` → 合成 → 返回 `audio/mpeg` 响应（不落盘） |
| `/api/sessions/{sid}/audio` | POST | 面试结束时上传整场录音（webm，≤500MB）→ 存 `audio/interview.webm`；幂等：已存在则覆盖 |
| `/api/sessions/{sid}/audio` | GET | 回放整场录音（`FileResponse`，支持 Range 由 FastAPI 处理） |
| `/api/styles` | GET | 风格列表（key/label），供前端渲染选择器 |
| `/api/backgrounds/{style}` | POST | 上传自定义动态背景（gif/mp4/webm/png，≤50MB）→ 存 `interviews/<uid>/backgrounds/<style>.<ext>` |
| `/api/backgrounds/{style}` | GET | 下发自定义背景文件；无则 404，前端回落 CSS 主题 |

- 路径安全：沿用现有 SID 校验 + `resolve()` + 越界检查 + `check_owner` 模式；`style` 参数用白名单 `^[\w]+$` 校验。
- 认证：全部走现有 `TokenAuthMiddleware`。

### 4. 风格参数流转

- `POST /api/session/start` 新增 `style` 表单字段 → `SessionManager.start_session(style=...)` → `SessionConfig` 新增 `style: str = "gentle"` 字段。
- `InterviewSession.start()` 调 `build_system_prompt(..., persona=get_style(style).prompt_fragment)`，把人设片段拼进系统提示词；`prompt.md` 自然携带。
- `init_transcript` 头部记录 `style`，历史回放可还原背景主题。
- TTS 音色：前端持有所选风格，`/api/tts` 由服务端按 `style` 解析音色/语速（避免前端硬编码音色名）。

### 5. 前端（`static/` 扩展）

**开始面板**：
- "面试官风格"四选一（默认温和）；
- "语音模式"复选框：勾选后开始面试前先请求麦克风+摄像头权限，失败则提示并可退回文本模式。

**面试页（语音模式）**：
- 面试官消息区背景 = 所选风格主题；有自定义背景文件则优先用 `<img>/<video>` 覆盖；
- 摄像头小窗（`<video muted autoplay>`）悬浮于聊天区角落，仅预览；
- "开始作答 / 结束作答"按钮（点击式）→ 结束后该段上传 `/api/asr` → 识别文字回填输入框并聚焦，用户可直接发送或修正后发送（识别错字不直接进面试记录）；
- 整场录制：`AudioContext` 把 TTS 音频同时接 `destination`（扬声器）与 `MediaStreamDestination`（录制），与麦克风流合并为一条 `MediaStream`，`MediaRecorder` 每 5 秒切片缓存在内存；面试结束（done 事件或点结束）后合并 Blob 一次上传。

**面试官语音**：SSE `delta` 文本按句末标点（。？！；.?!）切分，每凑满一句调 `/api/tts` 并顺序播放（播放队列，避免重叠）；TTS 失败静默降级为纯文字，不打断面试。

**历史/报告回放**：会话详情页若有 `audio/interview.webm` 则展示 `<audio controls>`。

**CSS 主题**（`style.css`）：四套 keyframes 动画 —— 严肃=深蓝冷光缓慢扫动；冷漠=灰白极简线条；温和=暖橙柔光呼吸；引导=渐变流动。

## 数据布局

```
interviews/<user_id>/<session_id>/
  transcript.jsonl        # 文字记录（现状不变，头部新增 style 字段）
  prompt.md / resume.md / report.md ...
  audio/
    interview.webm        # 整场混音音频（唯一留档音频）
interviews/<user_id>/backgrounds/
  serious.gif ...         # 可选自定义背景（按风格一个文件）
```

## 错误处理

- DashScope key 缺失 / ASR、TTS 调用失败：前端 toast 提示，语音功能降级，文本流程不受影响；
- ASR 识别为空：提示"没听清，请重说或改用打字"；
- 整场上传失败：重试 3 次，仍失败提示用户手动下载本地副本；
- 摄像头/麦克风权限拒绝：给出指引，语音模式不可用但文本模式正常；
- 上传超限：400 明确错误信息。

## 已知取舍

- 不戴耳机时麦克风会拾到扬声器的 TTS，回放中面试官声音有轻微重叠感（电子直混 + 环境拾音双路径）；本地单人练习可接受。
- MediaRecorder 5 秒切片仅缓存在内存，浏览器崩溃则丢失整场录音（页面正常关闭/刷新不触发；`beforeunload` 尽力上传已缓存的 Blob——实现为尽力而为，不承诺成功）。
- ASR 为录音文件识别（非实时流），每段回答有约 1-2 秒识别延迟，对面试问答节奏可接受。

## 测试

- `persona.py`：风格解析、未知 key 回落、提示词拼接进 `build_system_prompt` 的单测；
- `audio.py` + 端点：注入 fake client，测 ASR 临时文件清理、TTS 音色按风格解析、整场音频落盘路径与越界防护、背景上传白名单与 404 回落；
- `init_transcript` 记录 style 的单测；
- 前端：浏览器手动/GUI 验证（权限弹窗、录音、TTS 播放、背景切换、摄像头预览、音频回放）；无麦克风环境用 Chrome 假设备验证。

## 不做的事（YAGNI）

- 不做 WebRTC 实时流式 ASR/TTS；
- 不录制、不存储摄像头视频；
- 不做多音色试听、音色自定义参数面板；
- 不做飞书渠道的语音支持。
