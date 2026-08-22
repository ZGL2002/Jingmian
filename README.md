# 面试 Agent

终端文本版"模拟面试官"：粘贴简历后自由对话式练习后端技术面试（简历深挖优先、至少一道场景题、约 30% 通用后端与 AI 应用开发基础），至少 20 题后可自然收尾，结束生成评估报告。

## 安装

```bash
python -m pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
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
