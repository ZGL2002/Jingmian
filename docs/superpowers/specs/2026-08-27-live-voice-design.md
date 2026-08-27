# 流式实时语音面试（Live 模式）设计文档

日期：2026-08-27
状态：设计已在对话中确认（方案 A），用户批准实施
前置：2026-08-26-voice-video-interview-design.md（在其基础上升级语音链路）

## 背景与目标

现有语音模式是"对讲机式"回合（说完→等5秒静音→上传→整段识别→回填输入框确认→发送），一轮空档 8-10 秒，与用户期望的 GPT-Live 式实时交流差距大。本设计将语音链路升级为流式实时：

1. 边说边识别（流式 ASR，字幕实时刷新）；
2. 说完约 1-2 秒内自动发送（句尾事件 + 短静音窗口，无需手动确认）；
3. 面试官流式合成、首包即播；
4. 用户可随时插话打断面试官播报（barge-in）；
5. 保留既有全部能力：风格系统、摄像头预览、整场混音录音、重听、历史回放、报告、文本输入。

目标空档：一轮问答 ≤ 2.5 秒（静音判定 ~0.8s + ASR 收尾 ~0.3s + LLM 首 token ~0.5-1s + TTS 首包 ~0.3s）。

## 总体链路

```
麦克风 → AudioWorklet(16k PCM) ──(回声闸门控制)──► WS /api/ws/voice/{sid} ──► DashScope Recognition 流式
                                                  ◄── partial/final JSON ──── (max_sentence_silence=800)
客户端攒句 + 1.2s 静音窗口 → 自动 POST /api/answer（现有链路）
SSE delta → 句切分 → POST /api/tts/stream（SSE 返回 base64 PCM 块）→ 首包即播（缓存供重听）
播放期间：闸门关闭（不送识别流）；本地 RMS 检测到开口 → 停播 + 开闸（barge-in）
整场混音 MediaRecorder（麦克风 + TTS 直混）→ 结束上传 audio/interview.webm（不变）
```

## 模块设计

### 1. `web/audio.py` 扩展：流式识别器与流式合成

```python
class StreamRecognizer:
    """一个语音会话的流式识别器：PCM 帧 → DashScope Recognition 流式，回调产出中间/最终文本。"""
    def __init__(self, engine: DashScopeEngine, on_event: Callable[[dict], None]): ...
    def start(self) -> None      # Recognition.start()（SDK 内部线程收发）
    def feed(self, pcm: bytes) -> None   # send_audio_frame，16k 单声道 PCM16LE
    def stop(self) -> None       # stop()，SDK 线程安全
# 构造 Recognition 参数：model=engine.asr_model, callback=..., format='pcm',
# sample_rate=16000, max_sentence_silence=800, language_hints 按 fun-asr/qwen 适配（复用现有规则）
# 回调 on_event({"type": "partial"|"final", "text": str})，final = RecognitionResult.is_sentence_end

# DashScopeEngine 新增：
def synthesize_stream(self, text, voice, speed, on_chunk: Callable[[bytes], None]) -> None
# tts_v2 streaming_call + streaming_complete，format=PCM_22050HZ_MONO_16BIT，
# 每块音频字节经 on_chunk 透出（SDK 回调线程调用）
```

### 2. `web/voice_stream.py`（新）：WebSocket 语音通道

- 端点 `WS /api/ws/voice/{session_id}`，注册于 app.py。
- 鉴权：**BaseHTTPMiddleware 不处理 websocket scope**，端点内手工读 cookie 并 `token_matches`，失败 close(4401)。
- 会话不存在 close(4404)；audio 未配置 close(4403)；未 accept 前关闭。
- 协议：
  - 客户端→服务端：binary 帧 = PCM16LE 16k 单声道；文本帧 `{"type":"stop"}` 主动结束识别。
  - 服务端→客户端：`{"type":"partial","text":...}` / `{"type":"final","text":...}` / `{"type":"error","message":...}`。
- SDK 回调线程 → asyncio 队列（`loop.call_soon_threadsafe`）→ 逐条推给客户端；连接断开时 `StreamRecognizer.stop()`。

### 3. `/api/tts/stream`：流式合成（SSE）

- 入参与校验与 `/api/tts` 完全一致（text ≤500、style 音色三级解析）。
- 响应 `text/event-stream`：每个音频块一个 `data: <base64 PCM16LE 22.05k 单声道>` 事件，结束发 `data: [DONE]`。
- 合成在工作线程执行，`on_chunk` 经线程安全队列转发；错误在流中发 `{"type":"error"}` 后结束。
- 现有 `/api/tts`（整段 mp3）保留不动，作为兼容与回退。

### 4. 前端重写（voice.js + 新增 pcm-worklet.js）

**pcm-worklet.js**：AudioWorkletProcessor，每 128 帧Float32 postMessage；主线程攒 ~100ms 转 Int16 送 WS。AudioContext 以 `{sampleRate: 16000}` 创建（浏览器自动重采样麦克风）。

**LiveVoiceEngine（替换原 VoiceEngine）**：
- `enable()`：getUserMedia(audio+video)、建 16k AudioContext、worklet 接入、摄像头预览（不变）；
- `beginSession(sid, style)`：开 WS、启动 StreamRecognizer（服务端）、启动整场混音 MediaRecorder（不变）；
- **字幕与攒句**：`partial` 实时刷新 `#live-caption`；`final` 累积到待提交缓冲；每次 ASR 事件重置 1.2s 定时器，触发时缓冲非空 → 自动 `POST /api/answer` 并显示为候选人消息；
- **回声闸门**：TTS 播放期间不向 WS 送麦克风帧（本地继续录混音）；播报队列清空后 300ms 开闸；
- **barge-in**：播放期间主线程对 worklet 帧做 RMS 检测（沿用自适应阈值：0.5s 环境基线×3 与 0.01 取大），连续 150ms 超阈值 → 停掉全部已调度音频源、清空句队列、开闸；
- **流式播放**：每个句子 `fetch('/api/tts/stream')` 读 SSE，base64 → PCM 字节缓冲，凑满偶数字节即建 AudioBuffer(22050) 顺序调度（`ctx.destination` + `recDest`），首包即响；该轮 AudioBuffer 列表缓存供**重听**；
- `finish()`：关 WS、停识别、停录制并上传整场音频（不变）。

**UI 变化（index.html/app.js/style.css）**：
- 移除：🎤/⏹ 录音按钮、"自动结束作答"开关（被句尾事件取代）；
- 新增：`#live-caption`（输入框上方实时字幕条）、`#voice-status`（聆听中/面试官说话中，可打断 提示）；
- 保留：打字输入框（随时文字作答）、重听按钮、风格选择、背景、摄像头窗。

## 已知取舍

- 建议戴耳机：不戴时打断瞬间的识别可能混入面试官尾音（闸门挡住播报期间的常规拾音）；
- 自动发送：识别错字直接进面试记录，靠字幕可见 + 口头更正/下一轮打字补救；
- 句尾静音窗口 0.8s（服务端）+ 攒句 1.2s（客户端）为默认值，写死为常量，需要再调时改代码；
- 旧"点击录音"交互完全移除，语音模式即实时模式。

## 测试

- `audio.py`：fake dashscope 模块注入，测 StreamRecognizer 参数（format=pcm、max_sentence_silence、language_hints 适配）、feed→回调透传 partial/final；synthesize_stream 的 on_chunk 透传；
- `voice_stream.py`：TestClient websocket——鉴权失败 4401、会话不存在 4404、audio 未配置 4403、正常收发（fake engine 直接触发回调）；
- `/api/tts/stream`：fake engine 两块输出 → 两个 data 事件 + [DONE]；错误码与 /api/tts 一致；
- 前端：node 可静态验证切句逻辑（沿用）；状态机与真机体感由用户验收（清单见计划）。

## 不做的事（YAGNI）

- 不做服务端说话人分离/回声消除（靠闸门+耳机）；
- 不做 Qwen-Omni 端到端语音模型接入（另行立项）；
- 不做识别置信度展示、多语言切换 UI；
- 不改文本（打字）作答链路。
