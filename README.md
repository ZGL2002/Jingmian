# 面试 Agent

终端文本版"模拟面试官"：粘贴简历后自由对话式练习后端技术面试（简历深挖优先、至少一道场景题、约 30% 通用后端与 AI 应用开发基础），至少 20 题后可自然收尾，结束生成评估报告。

## 安装

```bash
python -m pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填入密钥：
#   DeepSeek：DEEPSEEK_API_KEY
#   阿里云百炼：INTERVIEW_PROVIDER=dashscope + DASHSCOPE_API_KEY（模型默认 qwen-plus，可选 qwen-max/qwen-turbo/deepseek-v3）
```

## 使用

```bash
python -m interview_agent
```

按提示粘贴简历（完成后输入一行 `END`），或输入 `.txt` / `.md` / `.pdf` 文件路径。

回答支持多行：粘贴整段回答后，按一个**空行**（直接回车）提交本条回答；直接回车不会提交内容。对话中输入 `结束`、`exit` 或 `/end` 可随时结束面试。

## 本场记录

每场面试保存在 `interviews/<user_id>/<时间戳_随机ID>/`：

- `resume.md`：简历副本
- `prompt.md`：实际使用的提示词
- `transcript.jsonl`：完整对话记录
- `answers/`：超长回答全文
- `report.md`：评估报告

## 安全

- API key 只从环境变量/`.env` 读取，永不写入任何记录文件。
- 工具只能访问本场会话目录（`interviews/<user_id>/...`），路径校验 + 符号链接解析，无法越界。
- 没有 shell / 任意命令执行工具。

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
  - **直接开口说话**：边说边识别，输入框上方实时显示字幕；停顿约 1-2 秒自动作为回答发送（无需点按钮）；
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

未配置 DashScope key 时语音功能自动禁用，文本面试不受影响。建议佩戴耳机，
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
