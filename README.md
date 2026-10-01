# 面试 Agent

终端文本版"模拟面试官"：粘贴简历后自由对话式练习后端技术面试（简历深挖优先、至少一道场景题、约 30% 通用后端与 AI 应用开发基础），至少 20 题后可自然收尾，结束生成评估报告。

## 安装

```bash
python -m pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填入密钥（三选一，详见下方「模型矩阵」）：
#   DeepSeek：DEEPSEEK_API_KEY
#   阿里云百炼：INTERVIEW_PROVIDER=dashscope + DASHSCOPE_API_KEY（模型默认 qwen-plus，可选 qwen-max/qwen-turbo/deepseek-v3）
#   智谱 GLM：INTERVIEW_PROVIDER=zhipu + ZHIPU_API_KEY（免费档 glm-4.7-flash，模型名以控制台为准）
```

## 模型矩阵

| 能力 | 云端方案 | 本地方案 |
|---|---|---|
| 对话 LLM（面试官 + 评估报告） | DeepSeek（默认）/ 阿里云百炼 qwen / 智谱 GLM 免费档 | — |
| 语音识别 ASR | 百炼 paraformer-realtime-v2 | FunASR runtime（自部署，CPU 即可实时） |
| 语音合成 TTS | 百炼 cosyvoice-v2 | CosyVoice（自部署，需 GPU） |

对话与语音两条通道互不绑定，可任意组合（如 智谱做对话 + 本地引擎做语音）。
评估报告与对话共用同一 LLM，切换 provider 自动生效；`.env` 改回旧 provider 即可秒级回退。

**语音本地部署**（`INTERVIEW_AUDIO_PROVIDER=local`，不设置时按有无 `DASHSCOPE_API_KEY` 自动推断）：

- `FUNASR_WS_URL`（默认 `ws://127.0.0.1:10095`）——FunASR runtime server：
  Docker 镜像 `registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr-runtime-sdk`，
  2pass 流式模式，自带 VAD/标点/数字正则化；
- `COSYVOICE_URL`（默认 `http://127.0.0.1:9880`）——CosyVoice server（CosyVoice2-0.5B，
  Turing+ 显卡 fp16 约 3GB 显存；Pascal 老卡 fp32 约 5GB），需暴露 OpenAI 兼容端点
  `POST /v1/audio/speech` 返回 WAV（如 [jianchang512/cosyvoice-api](https://github.com/jianchang512/cosyvoice-api)，
  默认端口 9933，用 `COSYVOICE_URL` 指向实际地址即可）；
  音色填本地 server 支持的名称，`INTERVIEW_TTS_VOICE` 全局或 `INTERVIEW_TTS_VOICE_<风格>` 按面试官风格指定。

本地模式已知差异：`/api/asr` 整段识别仅支持 wav/pcm（云端另支持 mp3 等）；
云端 ASR 的服务端去语气词不再生效（浏览器端纯语气词过滤仍保留，句内语气词可后续在 FunASR 后处理增强）。

## 使用

```bash
python -m interview_agent
```

按提示粘贴简历（完成后输入一行 `END`），或输入 `.txt` / `.md` / `.pdf` 文件路径。

回答支持多行：粘贴整段回答后，按一个**空行**（直接回车）提交本条回答；直接回车不会提交内容。对话中输入 `结束`、`exit` 或 `/end` 可随时结束面试。

## 简历 GitHub 项目深挖

简历项目里带 `github.com/<owner>/<repo>` 链接时（CLI / Web / 飞书三渠道通用），面试与代码审读**并行进行**：

- **开面试不等待**：点击开始/发送简历后面试立即开始；面试官提示词会注明"仓库后台审读中"，
  并先从项目整体架构、技术选型与通用后端基础问起；
- **回答期间后台审读**：代码审读子 agent 在守护线程中拉取仓库元信息与文件树（跳过依赖目录/二进制/超大文件），
  挑核心文件阅读，产出「项目结构 / 技术栈实证 / 实现亮点 / 与简历描述对照 / 建议提问点」分析；
- **完成后无缝注入**：分析在下一个人机轮次边界以系统消息送达面试官（不会打断进行中的问答），
  此后基于**实际代码**深挖实现细节并对照简历验证真实性；CLI 会打印一行提示，Web 端聊天区显示
  「GitHub 代码审读完成」；
- **面试进行中**面试官可调用 `list_github_files` / `read_github_file` 工具随时回读仓库代码继续追问
  （只能读本简历出现过的仓库，会话缓存优先）；
- 完整分析存于本场会话目录 `repos/<owner>__<repo>/analysis.md`，已读文件缓存在同级 `files/` 下；
  面试已收尾时才完成的分析会被丢弃（文件仍保留在会话目录）。

配置（`.env`，均有默认值）：`INTERVIEW_GITHUB_ANALYSIS=0` 可整体关闭；`INTERVIEW_GITHUB_MAX_REPOS`
限制单场最多分析仓库数（默认 3）；`GITHUB_TOKEN` 可选，用于提升 GitHub API 限流额度或访问私有仓库。
网络失败 / 私有仓库未配 token / 触发限流时该仓库自动跳过并在注入消息中注明，面试照常进行。

## 本场记录

每场面试保存在 `interviews/<user_id>/<时间戳_随机ID>/`：

- `resume.md`：简历副本
- `prompt.md`：实际使用的提示词
- `transcript.jsonl`：完整对话记录
- `answers/`：超长回答全文
- `repos/`：简历 GitHub 项目分析（`analysis.md`）与已读代码缓存（`files/`）
- `report.md`：评估报告

## 安全

- API key 只从环境变量/`.env` 读取，永不写入任何记录文件。
- 工具只能访问本场会话目录（`interviews/<user_id>/...`），路径校验 + 符号链接解析，无法越界。
- 没有 shell / 任意命令执行工具。
- GitHub 仓库深挖只发起指向 `api.github.com` 的只读 HTTPS GET（宿主白名单 + 请求预算），
  且只能读取本简历中出现的仓库；`GITHUB_TOKEN` 同样只从环境变量读取。

## Web 界面

```bash
python -m pip install -e ".[dev]"
# .env 中配置 DEEPSEEK_API_KEY 与 INTERVIEW_WEB_TOKEN 后：
python -m interview_agent.web
```

浏览器访问 `http://<服务器IP>:8765`，输入 `INTERVIEW_WEB_TOKEN` 登录。
支持：目标公司/岗位、简历粘贴或上传、JD、面经参考（直接粘贴或从面经库勾选）、
流式问答、一人多场并行、历史列表与报告回看、本地面经库管理。

### 语音/视频面试（语音模式）

在 `.env` 配置 `DASHSCOPE_API_KEY`（阿里云百炼，语音识别 Paraformer + 语音合成 CosyVoice；
可与 DeepSeek 对话 LLM 并用）后，Web 端"开始面试"前可：

- **选择面试官风格**：严肃（默认）/ 冷漠 / 温和 / 引导——同时决定动态背景、面试官提问人设与语音音色；
  每种风格还可上传自定义动态背景（gif/mp4/webm/png）覆盖内置动画；
- **勾选语音模式（实时语音对话）**：浏览器授权麦克风+摄像头后，像真人语音面试一样交流——
  - **直接开口说话**：边说边识别，输入框上方实时显示字幕；说完停顿约 5 秒自动作为回答发送（无需点按钮），
    回答中途停顿几秒再继续也不会被截断——若面试官已开始回应，你的续说会在其本轮结束后自动补发；
    听题时下意识的"嗯/呃"等纯语气词会被自动丢弃（识别层去语气词 + 发送前过滤），不会顶掉这道题；
  - **面试官即时开口**：流式合成、首包即播，说完约 1-2 秒内开始回答；
  - **可随时插话打断**：面试官播报中你一开口，播报立即停止并开始听你说；
  - 打字输入框保留，随时可切换文字作答；听不清点消息下方「🔊 重听」；
  - 摄像头画面仅本地实时预览，不录制不存储；
  - 整场面试（双方声音混音）录制为一个 `audio/interview.webm`，保存在本场会话目录 `audio/` 下，
    与 `transcript.jsonl` 文字记录同目录；历史页标记「音频✓」并可回放。

**实时语音建议佩戴耳机**：不戴时面试官声音会被麦克风拾到，打断瞬间可能混入少量面试官尾音。
识别文字自动发送，错字可直接从下一轮口头更正或改用打字。

**账号音色受限时**：默认四风格各用一个音色（cosyvoice-v2）；若你的百炼账号/模型只有单一音色，
配置 `INTERVIEW_TTS_VOICE=可用音色` 即可让四风格共用，风格差异仍由背景、人设、语速保留；
也可用 `INTERVIEW_TTS_MODEL` 更换 TTS 模型、`INTERVIEW_TTS_VOICE_<风格>` 按风格指定。
**换 TTS 模型必须同时换音色**（音色不跨模型兼容）：cosyvoice 系列用 longshu_v2 等；
qwen-audio-3.0-tts-flash/plus 用其自身音色表（如 longanfengyue、longanlingxin、longanlufeng）。
ASR 识别模型可用 `INTERVIEW_ASR_MODEL` 更换（默认 paraformer-realtime-v2，
可选 fun-asr-realtime、qwen-audio-3.0-asr-flash-streaming 等）。

语音服务两种形态：`.env` 配置 `DASHSCOPE_API_KEY` 走云端（Paraformer + CosyVoice）；
或按上文「模型矩阵」本地部署 FunASR + CosyVoice 后设 `INTERVIEW_AUDIO_PROVIDER=local` 全本地运行。
两者都未配置时语音功能自动禁用，文本面试不受影响。建议佩戴耳机，
否则回放中面试官声音会因麦克风拾到扬声器而有轻微重叠。

## 飞书渠道

```bash
python -m pip install -e ".[dev]"
# .env 中配置 DEEPSEEK_API_KEY、FEISHU_APP_ID、FEISHU_APP_SECRET 后：
python -m interview_agent.feishu
```

开放平台配置步骤（一次性）：

1. [飞书开放平台](https://open.feishu.cn/) → 创建**企业自建应用** → 记下 `App ID` / `App Secret` 填入 `.env`。
2. 应用能力 → 添加**机器人**。
3. 权限管理 → 开通：`im:message.p2p_msg:readonly`（读取用户发给机器人的单聊消息，**接收消息必需**）、`im:message:send_as_bot`（以机器人身份发消息）。
4. 事件与回调 → 事件配置 → 订阅方式选择**使用长连接接收事件** → 添加事件 `im.message.receive_v1`（接收消息）。
5. 版本管理与发布 → 创建版本并发布，可用范围设为全员（或按需）。

使用：在飞书中搜索机器人名称，私聊发送 `开始面试`，按引导回答即可；`帮助` 查看全部命令。面试记录保存在 `interviews/<飞书用户 open_id>/`，与 Web 渠道互不影响。
