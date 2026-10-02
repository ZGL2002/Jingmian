# 模型修改计划：对话 LLM → GLM（智谱）+ 语音模型本地部署

> 目标拓扑：对话 LLM 走智谱开放平台免费档（云端），语音识别/合成全部本地部署（FunASR + CosyVoice）。
> 硬件假设：本地 GPU 约 11GB 显存（代际待确认，见阶段 0）。
> 原则：每个阶段独立可交付、可单独回退；全程 pytest 护航（当前 269 用例全绿）。

---

## 现状（代码事实）

两条独立协议通道，互不绑定：

| 通道 | 现实现 | 关键代码 |
|---|---|---|
| 对话 LLM | DeepSeek（`deepseek-chat`，默认）/ 百炼（`qwen-plus`），OpenAI 兼容协议 | `llm.py:117-137`：`OpenAICompatibleClient` 基类 + `DeepSeekClient` / `DashScopeClient` + `create_llm` 工厂 |
| 语音 | DashScope SDK 专有协议：`paraformer-realtime-v2`（流式 ASR）+ `cosyvoice-v2`（流式 TTS） | `web/audio.py`：`DashScopeEngine`（唯一引擎实现）+ `AudioService.from_config`（无 key 返回 None 整体禁用） |

语音链路的已知问题（本计划阶段 2 解决）：

- `StreamRecognizer.start()`（`web/audio.py:128-155`）**硬编码 import dashscope 的 `Recognition`**，
  未走引擎抽象——接本地 ASR 前必须先把流式识别下沉到引擎层；
- `web/app.py:102,122` 的 404 文案写死「未配置 DASHSCOPE_API_KEY」，本地模式需同步修改。

前端契约（`web/static/voice.js`，**本计划不改动**，本地引擎必须适配）：

- 上行：16kHz 单声道 PCM16，100ms 批；
- ASR 事件协议：`{"type": "partial"|"final"|"error", "text"/"message": str}`；
- 下行 TTS：按句请求 `/api/tts/stream`，SSE 下发 base64 的 **22.05kHz 单声道 PCM16**（`TTS_RATE=22050`）；
- 攒句提交窗口 4.2s、句尾静音 800ms（`STREAM_SILENCE_MS`）、5s 全零帧保活——均为客户端/服务端通用逻辑，本地引擎同样适用。

---

## 阶段 0：前置准备（不改代码）

1. **确认 GPU 代际**：`nvidia-smi --query-gpu=name,compute_cap --format=csv`
   - Turing 及以上（算力 ≥7.5）：CosyVoice 跑 fp16（约 3GB）；
   - Pascal（1080 Ti，算力 6.1）：fp16 无加速，CosyVoice 用 fp32（约 5GB，仍在 11GB 预算内）。
2. **智谱账号**：开通 bigmodel.cn，记录免费模型名（当前为 `glm-4.7-flash`，以控制台为准，
   `glm-4-flash` 为备选）；确认 function calling 可用（面试官 Agent 硬依赖 tool_calls）。
3. **部署 FunASR runtime server**（ASR，CPU 即可跑实时）：
   Docker 镜像 `registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr-runtime-sdk`，
   WebSocket 端口默认 10095，2pass 流式模式，自带 VAD/标点。
4. **部署 CosyVoice server**（TTS，GPU）：CosyVoice2-0.5B（或 300M），以 HTTP/WebSocket server
   形式常驻；准备 4 个面试官风格对应的音色（内置音色或参考音频）。

**验收**：FunASR 官方 demo 客户端能实时转写麦克风；CosyVoice 能按句合成 22.05kHz 可用音频。

---

## 阶段 1：对话 LLM 接入 GLM（约 0.5 天，独立可交付）

改动三处：

1. `llm.py`：新增子类 + 工厂分支
   ```python
   class ZhipuClient(OpenAICompatibleClient):
       BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
       LABEL = "智谱"
   ```
   `create_llm()`（`llm.py:135`）加 `if provider == "zhipu": return ZhipuClient(...)`。
2. `config.py`：`PROVIDER_KEY_ENV` 增加 `"zhipu": "ZHIPU_API_KEY"`。
3. `.env` / `.env.example`：
   ```
   INTERVIEW_PROVIDER=zhipu
   ZHIPU_API_KEY=sk-xxx
   INTERVIEW_MODEL=glm-4.7-flash
   ```

测试与验收：

- `tests/test_llm.py` 增加 `ZhipuClient` 路由用例（沿用现有 fake 模块注入方式）；
- `tests/test_config.py` 增加 provider→key 映射用例；
- **手测一场完整文本面试**，重点验证：面试官工具调用正常（`request_wrap` 收尾、GitHub 工具）、
  单问规则等提示词纪律的遵循质量；评估报告生成正常（`evaluate.py` 与对话共用同一 LLM，自动生效）。

风险与回退：

| 风险 | 表现 | 回退 |
|---|---|---|
| 免费档指令遵循退化 | 违反单问规则、草率收尾、工具误用 | `.env` 改回 `INTERVIEW_PROVIDER=deepseek`，秒级回退 |
| 免费档限流/日额度 | 高频使用 429 | 同上；或降级 `glm-4-flash` |

---

## 阶段 2：语音引擎抽象重构（约 0.5 天，行为不变的前置重构）

1. **流式识别下沉到引擎**：`StreamRecognizer`（`web/audio.py:114-164`）不再 import dashscope；
   引擎层新增工厂方法（如 `engine.create_stream_recognizer(on_event) -> {start, feed, stop}`），
   DashScope 现有 `_Cb` 回调逻辑整体挪入 `DashScopeEngine`；
   `voice_stream.py` 的 `StreamRecognizer(audio.engine, on_event)` 改为薄包装，调用引擎工厂。
2. **`AudioService.from_config` 分支**：新增 `INTERVIEW_AUDIO_PROVIDER`（默认按现状推断：
   有 `DASHSCOPE_API_KEY` 即 dashscope；显式 `local` 走本地引擎，阶段 3 实现）。
3. **文案**：`web/app.py:102,122` 的 404 提示按 provider 动态生成。

验收：

- 现有 fake dashscope 注入测试（`tests/test_web_audio_service.py`）全部保持绿色；
- 用 DashScope 跑一场语音面试回归：打断、攒句、补发、重听、整场录制全功能正常。

---

## 阶段 3：LocalEngine 实现——FunASR + CosyVoice（1–2 天）

新增 `web/local_audio.py`（或并入 `audio.py`），`LocalEngine` 实现与 `DashScopeEngine` 相同的接口：

1. **流式 ASR**：WebSocket 客户端连 FunASR runtime（`FUNASR_WS_URL`，默认 `ws://127.0.0.1:10095`）；
   16k PCM16 帧直传；2pass 结果映射为 `{"type":"partial"|"final"}` 事件协议；连接错误映射 `error` 事件
   （触发既有断线重连路径）；**全零保活帧语义验证**（FunASR 同样有会话闲置超时）。
2. **流式 TTS**：HTTP/WS 调本地 CosyVoice server（`COSYVOICE_URL`），按句合成；
   输出重采样至 22.05kHz 单声道 PCM16 后经 `on_chunk` 透出；首句延迟目标 < 2s。
3. **整段合成/整文件识别**：`synthesize`（试听用）与 `transcribe_file` 分别接 CosyVoice、FunASR 非流式接口。
4. **音色映射**：复用现有 `INTERVIEW_TTS_VOICE` / `INTERVIEW_TTS_VOICE_<STYLE>` 机制
   （`config.py:40-47`、`persona.resolve_voice`），填本地音色名或参考音频 ID。
5. **去语气词补偿**：云端 `disfluency_removal_enabled` 能力丢失，客户端 `isFillerOnly`
   （`voice.js`）继续兜底纯语气词；可选增强：FunASR 后处理过滤句内语气词。

配置（`.env`）：
```
INTERVIEW_AUDIO_PROVIDER=local
FUNASR_WS_URL=ws://127.0.0.1:10095
COSYVOICE_URL=http://127.0.0.1:9880
INTERVIEW_TTS_VOICE_SERIOUS=<本地音色1>
INTERVIEW_TTS_VOICE_COLD=<本地音色2>
INTERVIEW_TTS_VOICE_GENTLE=<本地音色3>
INTERVIEW_TTS_VOICE_GUIDE=<本地音色4>
```

测试与验收：

- fake WebSocket/HTTP server 注入，测 `LocalEngine` 的事件协议、PCM 契约、错误映射；
- **完整语音面试一场回归**：实时字幕、5s 攒句提交、打断（150ms 能量 VAD 为客户端逻辑，不受影响）、
  续说补发（含「补充上一题」标记）、断线重连、重听、整场混音录制；
- 指标：字幕首字 < 1s，面试官说完到开口 < 2s。

---

## 阶段 4：收尾（0.5 天）

- README「模型矩阵」更新：对话 GLM（免费档）/ 语音本地部署，含部署依赖说明（FunASR、CosyVoice）；
- `.env.example` 增补全部新配置项；
- 全量 pytest 通过；git 分阶段提交（每阶段一个 commit，均可独立回退）。

---

## 工作量与里程碑汇总

| 阶段 | 内容 | 工作量 | 交付物 |
|---|---|---|---|
| 0 | 硬件确认 + 三套账号/服务就绪 | 0.5 天 | 本地 ASR/TTS 服务可访问 |
| 1 | GLM provider 接入 | 0.5 天 | 文本面试跑在 GLM 上 |
| 2 | 引擎抽象重构 | 0.5 天 | DashScope 行为不变、抽象就位 |
| 3 | LocalEngine | 1–2 天 | 语音全本地、全功能回归 |
| 4 | 文档与收尾 | 0.5 天 | 合计约 3–4 天 |

## 总风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| GLM 免费档指令遵循退化（面试官纪律） | 中 | 阶段 1 手测卡点；`.env` 秒级回退 DeepSeek |
| GLM 免费档限流/不公开额度 | 中 | 备选 `glm-4-flash`；回退付费档 |
| FunASR runtime 协议/版本变动 | 低 | 锁定镜像版本；阶段 3 的 fake 测试固化协议契约 |
| Pascal 卡 fp16 不可用 | 低 | 阶段 0 确认；CosyVoice fp32 显存约 5GB 仍可行 |
| 本地 TTS 首句延迟超标 | 中 | 按句合成天然缓解；超预算则 TTS 退 piper（CPU）、CosyVoice 仅离线试听 |
| 云端去语气词能力丢失 | 低 | 客户端过滤已有；可选 FunASR 后处理增强 |
