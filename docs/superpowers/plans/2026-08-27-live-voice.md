# 流式实时语音面试（Live 模式）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把语音面试从"录音后识别"升级为流式实时：边说边出字、自动发送、流式合成首包即播、可插话打断；保留全部既有能力。

**Architecture:** 后端加 WebSocket 语音通道（浏览器 PCM ↔ DashScope Recognition 流式识别）与 SSE 流式合成端点；前端 voice.js 重写为 LiveVoiceEngine（AudioWorklet 采集 16k PCM、回声闸门、barge-in、攒句自动提交、PCM 块顺序播放）。会话循环/SSE/存储/风格系统全部复用。

**Tech Stack:** FastAPI WebSocket / dashscope Recognition 流式（format=pcm, max_sentence_silence=800）/ tts_v2 streaming_call（PCM_22050HZ_MONO_16BIT）/ AudioWorklet / SSE。

**Spec:** `docs/superpowers/specs/2026-08-27-live-voice-design.md`（协议、常量、取舍以其为准）

## Global Constraints

- 识别流参数固定：`format="pcm"`、`sample_rate=16000`、`max_sentence_silence=800`；`language_hints` 复用现有 fun-asr 单语种规则。
- 攒句窗口 1200ms（客户端常量）、闸门重开延迟 300ms、barge-in 判定连续 150ms 超阈值（阈值=max(0.5s 环境基线×3, 0.01)）。
- WS 鉴权：端点内手工校验 cookie（`AUTH_COOKIE` + `token_matches`），失败 close 4401；会话不存在 4404；audio 未配置 4403。
- `/api/tts/stream` 校验与 `/api/tts` 一致；SSE 事件 `data: <base64 PCM>`，结束 `data: [DONE]`，错误 `data: {"type":"error",...}`。
- 现有 `/api/tts`、整场混音上传、风格/背景/重听/历史接口不动；移除前端 🎤 录音按钮与"自动结束作答"开关。
- 197 个既有测试保持全绿；新功能测试注入 fake dashscope，不触网。

## Tasks

### Task 1: audio.py 流式识别与流式合成（+测试）

- [ ] 失败测试 `tests/test_web_audio_stream.py`：fake dashscope（Recognition 支持 start/send_audio_frame/stop + 回调、RecognitionResult.is_sentence_end、tts_v2.ResultCallback）：
  - `StreamRecognizer(engine, on_event)`：start 后 kwargs 断言（format=pcm、max_sentence_silence=800、language_hints 适配 fun-asr/qwen）；feed(bytes) 触发 fake 回调 → on_event 收到 `{"type":"final"|"partial","text"}`；stop 调用透传。
  - `engine.synthesize_stream(text, voice, speed, on_chunk)`：on_chunk 依次收到 fake 的两块字节；kwargs 断言 model/voice/speech_rate。
- [ ] 实现：`StreamRecognizer`（构造 Recognition 时 `callback=` 必填；`_RecognitionCallbackBridge` 把 on_event 翻译成 partial/final/error dict）；`DashScopeEngine.synthesize_stream`（`streaming_call`+`streaming_complete`，`format=AudioFormat.PCM_22050HZ_MONO_16BIT`）。
- [ ] 全量 pytest 绿后提交 `feat: 流式识别器与流式合成`。

### Task 2: WebSocket 语音通道（+测试）

- [ ] 失败测试 `tests/test_web_voice_ws.py`：TestClient `websocket_connect`——未登录 4401；会话不存在 4404；audio=None 4403；正常路径（fake dashscope 的 Recognition 在 send_audio_frame 里同步回调 final）→ 客户端收到 `{"type":"final",...}`；发送文本 `{"type":"stop"}` 后连接关闭且 fake 的 stop 被调用。
- [ ] 实现 `interview_agent/web/voice_stream.py`：`run_voice_websocket(websocket, session_id, *, audio, token_matches_fn, session_exists)`——accept、`StreamRecognizer`（on_event 经 `loop.call_soon_threadsafe` 入 asyncio.Queue）、pump 协程 send_json、receive 循环喂 bytes、finally `rec.stop()`；`rec.start` 用 `asyncio.to_thread` 防阻塞事件循环。app.py 注册 `@app.websocket("/api/ws/voice/{session_id}")` 做四道守卫后调用。
- [ ] 提交 `feat: WebSocket 流式语音通道`。

### Task 3: /api/tts/stream SSE 端点（+测试）

- [ ] 失败测试 `tests/test_web_tts_stream.py`：fake engine.synthesize_stream 两块 → 响应含两个 `data:`（base64 可解回）+ `data: [DONE]`；引擎抛错 → `{"type":"error"}` 后结束；404/400 与 /api/tts 一致；音色按 style 三级解析（复用断言 longyingjing）。
- [ ] 实现：工作线程跑 synthesize_stream，`queue.Queue` 转发，`StreamingResponse(media_type="text/event-stream")`。
- [ ] 提交 `feat: 流式语音合成端点`。

### Task 4: pcm-worklet.js + voice.js 重写（LiveVoiceEngine）

- [ ] `static/pcm-worklet.js`：AudioWorkletProcessor，postMessage Float32 帧。
- [ ] `static/voice.js` 全量重写：LiveVoiceEngine（16k AudioContext、worklet→WS 批量发送（≥3200 字节 flush）、partial→onCaption、final→攒句+1200ms 自动 onSubmit、回声闸门、barge-in RMS、`/api/tts/stream` SSE 解析→PCM 块 AudioBuffer(22050) 顺序调度（destination+recDest）、turnBuffers 缓存重听、endTurn/finish 兼容旧接口、splitSentences/encodeWav 保留（encodeWav 供无流式回退不需要则删）。
- [ ] `node --check` + splitSentences 控制台用例。
- [ ] 提交 `feat(web): 实时语音引擎`。

### Task 5: app.js / index.html / style.css 集成

- [ ] index.html：删 🎤 按钮与 autostop 开关；answer-row 上方加 `<div id="voice-status">` 与 `<div id="live-caption">`；引入 pcm-worklet 由 voice.js 动态 addModule。
- [ ] app.js：LiveVoiceEngine 接线（onCaption/onSubmit/onState）、移除 toggleRecording/stopAnswerAndRecognize/cfg-autostop 引用、setControls 去掉 btn-record；startInterview/resume/结束逻辑保持。
- [ ] style.css：字幕条与状态条样式。
- [ ] live 服务器 HTTP 冒烟 + 全量 pytest。
- [ ] 提交 `feat(web): 实时语音模式集成`。

### Task 6: 文档与验收

- [ ] README 语音节改写为实时模式说明（戴耳机建议、打断方式、字幕、自动发送）；`.env.example` 无新增键。
- [ ] 全量 pytest + 手动验收清单交用户真机验证（边说出字/自动发送/插话打断/重听/整场音频）。
- [ ] 提交 `docs: 实时语音模式说明`。
