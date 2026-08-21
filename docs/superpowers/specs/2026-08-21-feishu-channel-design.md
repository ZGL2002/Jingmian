# 面试 Agent 飞书渠道设计文档

日期：2026-08-21
状态：待用户评审
上游：2026-08-06-interview-agent-design.md（CLI 版设计）、2026-08-07-web-interface-design.md（Web 渠道设计，其中预留"飞书适配器"扩展点）

## 1. 背景与目标

CLI 与 Web 渠道已具备完整面试闭环。本阶段新增 **飞书渠道**：企业成员在飞书里私聊机器人，通过对话式引导完成"开始面试 → 问答 → 结束 → 报告"全流程，让面试练习发生在日常沟通工具里。

核心成功标准：

1. 企业成员私聊机器人即可完成一整场面试（对话式引导配置 → 问答 → 卡片报告），无需任何网页或命令行。
2. 多个成员各自独立面试互不干扰；每人同时只有一场活跃面试。
3. Agent 核心与 Web 层**零改动**，新增代码收敛在 `interview_agent/feishu/` 包与配置/依赖声明内。
4. 服务器无需公网 IP（飞书长连接模式），部署门槛与 Web 版一致。
5. 为语音消息面试（ASR/TTS）预留清晰接口边界，后续为纯增量扩展。

## 2. 范围

### 本版包含

- 飞书企业自建应用 + 机器人能力，**长连接模式**（`lark-oapi` SDK 的 `lark.ws.Client`）接收私聊消息事件，无需公网回调地址。
- 对话式引导开场：目标公司（可跳过）→ 岗位名称（可跳过）→ 简历粘贴（多条拼接，单独一行 `END` 结束，可跳过）→ 自动开始面试。
- 问答输出：每回合先发"面试官思考中…"占位消息，LLM 生成完毕后 **PATCH 该消息**为完整问题（一回合 2 次 API 调用，无频控风险）。
- 结束后评估报告以 **markdown 卡片消息**发送全文；`report.md` 照常落盘服务器。
- 全企业成员可用：飞书 `open_id` 作 `user_id`，存储与路径隔离沿用现有体系。
- 保留命令：`开始面试` / `结束`（含 `exit`、`/end`）/ `帮助`（含 `help`）；引导过程中 `取消`；简历收集中 `END` / `跳过`。
- 事件去重（`event_id`）、非文本消息友好提示、群聊消息忽略（v1 仅私聊）。

### 本版明确不做（未来扩展）

- 语音消息面试（ASR/TTS）——仅预留接口边界。
- 用户白名单 / 费用配额控制（当前全员可用，LLM 费用由服务器统一承担）。
- 群聊中 @ 机器人面试（需 chat 维度会话路由）。
- 飞书内历史查询、面经库管理、JD 输入（引导流程只收集公司/岗位/简历三项）。
- Web 端实时语音/视频面试（属 Web 渠道未来的独立子系统，见第 14 节背景说明）。

## 3. 需求汇总

| 维度 | 决定 |
| --- | --- |
| 应用形态 | 企业自建应用 + 机器人；长连接接收事件（无公网 IP 依赖） |
| 使用者 | 全企业成员；`open_id` 作 `user_id` |
| 开场方式 | 对话式引导：公司 → 岗位 → 简历（均可跳过） |
| 输出节奏 | "思考中"占位消息 + 回合结束 PATCH 为完整内容 |
| 报告交付 | markdown 卡片消息发全文 + 服务器落盘 |
| 并发 | 多人多场并行；单用户同时仅一场活跃 |
| 渠道关系 | 与 Web 完全独立进程，可各自启停；共享同一 `interviews/` 存储与核心层 |
| 语音预留 | 入站消息规范化边界 + 出站渲染器边界，v1 仅实现文本 |
| 技术栈 | `lark-oapi` 官方 SDK（WS 长连接 + 消息 API） |

## 4. 架构总览

核心原则不变：**Agent 大脑与渠道无关，飞书只是新的表现层**。上一版设计预留的"飞书适配器"在此落地。

```
飞书用户（私聊机器人）
   ▲ 发消息                ▼ 机器人回复
飞书开放平台（事件订阅：长连接 WebSocket）
   │
lark.ws.Client（SDK 内置自动重连）
   ▼
FeishuBot（interview_agent/feishu/，新增）
   ├─ 消息规范化：飞书消息类型 → 文本        ← 语音预留边界（未来 audio→ASR）
   ├─ OnboardingFlow：对话式引导状态机
   ├─ 命令路由 / 单活跃会话约束 / 事件去重
   └─ 事件泵线程：EventQueue 事件 → 飞书消息   ← 语音预留边界（未来文本→TTS）
        │
        ▼ 复用现有接口（核心与 Web 层零改动）
渠道无关运行时（现位于 interview_agent/web/ 包内）：
SessionManager / InterviewTask / EventQueue
        ▼
Agent 核心：ToolAgent / InterviewSession / 工具注册表 / 简历解析 / 评估 / 存储
```

**关于复用 `web/` 包的说明**：`SessionManager`、`InterviewTask`、`EventQueue` 三个模块虽在 `interview_agent/web/` 包内，但本身不依赖任何 HTTP/FastAPI 概念，是渠道无关的运行时。v1 飞书包直接 import 复用，不做包迁移；若未来出现第三渠道再考虑将其提升为 `runtime/` 公共包。

## 5. 核心组件与职责

### 5.1 新增模块（`interview_agent/feishu/`）

| 模块 | 职责 |
| --- | --- |
| `__main__.py` | 入口：加载配置 → 校验 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` → 创建 LLM 与 FeishuBot → 启动 WS 长连接（阻塞运行） |
| `feishu_client.py` | `FeishuClient` 抽象接口（`send_text` / `patch_text` / `send_markdown_card`）+ `LarkFeishuClient` 实现（封装 `lark-oapi` 消息 API）；测试用内存 mock 实现 |
| `onboarding.py` | `OnboardingFlow` 每用户引导状态机：`ASK_COMPANY → ASK_POSITION → COLLECT_RESUME → 完成`；纯逻辑（输入文本 → 待发回复列表 + 可选的会话参数），不发消息，便于单测 |
| `bot.py` | `FeishuBot`：WS 事件入口（解析 open_id/文本、去重、只处理 p2p 私聊）；按"命令 / 引导中 / 面试中 / 空闲"分发；维护每用户活跃会话表；每场面试启动一个事件泵线程 |

### 5.2 事件泵（`bot.py` 内）

消费 `InterviewTask.queue`（复用 `events_after(last_seq)` 接口），把事件翻译为飞书消息：

| 事件 | 动作 |
| --- | --- |
| `status: thinking` 或首个 `delta` | 发送"面试官思考中…"占位消息，记下 `message_id`（开场白无 thinking 前导，故首个 delta 也触发） |
| `delta` | 累积到缓冲区（不调用 API） |
| `turn_end` | `patch_text(message_id, 完整回合文本)`；PATCH 失败则降级为直接发送新消息 |
| `status: evaluating` | 发送"面试结束，评估生成中…" |
| `status: done` | 从会话目录读 `report.md` → 发送 markdown 卡片；清理该用户活跃状态；泵退出 |
| `error` | 发送错误提示文本；清理活跃状态；泵退出 |
| 等待超时且 `task.ended` | 发送"本场面试已结束（空闲超时或异常）"；清理；泵退出（空闲回收路径在 runner 内直接 return、不发事件，泵需自查 `task.ended`） |

`done` 事件携带的 `report_url` 是 Web 相对路径，飞书侧忽略，直接读文件。

### 5.3 现有代码改动（最小集）

1. **`config.py`**：`load_config` 增加 `feishu_app_id`、`feishu_app_secret` 两项。
2. **`.env.example`**：新增飞书配置段与申请步骤注释。
3. **`pyproject.toml`**：主依赖加 `lark-oapi>=1.4`。
4. **`README.md`**：新增飞书渠道章节（应用创建、权限清单、事件订阅配置、启动命令）。

Agent 核心（`agent.py` / `session.py` / `llm.py` / `evaluate.py` 等）与 `web/` 包零改动。

## 6. 数据流（一次完整飞书面试）

1. **开始**：用户私聊"开始面试" → 若已有活跃面试，提示先"结束"；否则进入引导，问目标公司。
2. **引导**：用户依次回复公司（可"跳过"）、岗位（可"跳过"）、简历（多条消息拼接，单独一行 `END` 结束，或"跳过"用无简历模式）。引导中发"取消"放弃。
3. **建会话**：`bot` 调 `manager.start_session(open_id, company=…, position=…, resume_text=…)`（复用 Web 全套：目录创建、`prompt.md`、`resume.md`、transcript 初始化）→ 记为该用户活跃会话 → 启动事件泵 → 开场白以占位+PATCH 输出。
4. **问答循环**：用户每条私聊即一条回答 → `manager.submit_answer(open_id, session_id, text)`；`busy`（面试官思考中）时回复提示；泵按 5.2 输出问题。长回答落盘、上下文保护压缩等行为与 Web 完全一致。
5. **结束**：用户发"结束"（或模型自然 wrap）→ `status: evaluating` → 评估落盘 → `done` → 报告卡片 → 状态清理。
6. **落盘**：全程 `interviews/<open_id>/<session_id>/`，结构与 Web 会话相同。

## 7. 存储结构

无变化。沿用 `interviews/<user_id>/` 命名空间，飞书用户的 `user_id` 即 `open_id`（`ou_` 前缀 + 字母数字，字符集安全，无需转义）。owner 元数据、`PathPolicy` 路径校验、跨用户隔离规则全部沿用。Web 与飞书两个进程指向同一 `INTERVIEW_ROOT` 时，同一用户在两端的历史自然互通（v1 不做跨端会话接管，各自独立开新场）。

## 8. 命令与消息协议

| 用户输入 | 语境 | 行为 |
| --- | --- | --- |
| `开始面试` | 空闲 | 进入对话式引导 |
| `开始面试` | 引导中 | 重新开始引导（回到问公司一步） |
| `开始面试` | 面试中 | 提示先发"结束" |
| `结束` / `exit` / `/end` | 面试中 | 结束当前面试并评估（与 CLI 语义一致） |
| `结束` / `exit` / `/end` | 引导中 | 等同 `取消`，放弃引导 |
| `帮助` / `help` | 任意 | 发送使用说明 |
| `取消` | 引导中 | 放弃引导 |
| `跳过` | 引导问答 / 简历收集 | 跳过当前项（简历跳过 = 无简历模式） |
| `END`（单独一行） | 简历收集 | 结束简历收集并开始面试 |
| 任意文本 | 面试中 | 作为回答提交 |
| 任意文本 | 空闲 | 引导用户发"开始面试" |
| 任意文本 | 引导中 | 作为引导当前步的输入 |
| 非文本消息 | 任意 | 回复"暂仅支持文字消息"（语音为未来扩展） |
| 群聊消息 | — | 忽略（v1 仅处理 `chat_type == "p2p"`） |

匹配优先级：保留命令精确匹配最先，其次按语境分发。已知取舍：面试进行中回答恰好是"结束"二字会直接终止面试（CLI/Web 已是同一语义，保持一致，不加确认步骤）。

## 9. 并发与恢复

- **多人多场**：`SessionManager` 按 `open_id` 天然隔离；每场独立线程、独立事件队列。
- **单用户单场**：`FeishuBot` 维护 `open_id → 活跃 session_id` 表；开新场前必须结束当前场。私聊窗口内多场并行必然串台，此约束是产品规则而非技术限制。
- **空闲回收**：沿用 `SessionManager` sweep（默认 30 分钟）；泵线程通过 `task.ended` 自查感知并通知用户。
- **进程重启**：活跃会话丢失（与 Web 行为一致），transcript 与报告已落盘不丢；用户重新"开始面试"即可。
- **长连接断开**：SDK 内置自动重连；期间消息由飞书侧补推，事件去重兜底。

## 10. 安全设计

- **应用凭证**：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` 只从 `.env` / 环境变量读取，缺失时启动报错退出；永不写入任何记录、日志、报告。
- **存储隔离**：沿用 owner 元数据 + `PathPolicy` 强校验；飞书用户与 Web 用户（如有）互不可见对方记录（`open_id` ≠ `local`）。
- **工具面**：沿用白名单（仅会话目录内文件工具，无 shell）。
- **输入面**：命令为固定白名单，无任意指令执行；机器人只响应私聊，避免群内误触发与信息泄露。
- **费用暴露**：全员可用意味着全员可消耗 LLM 配额；v1 接受此风险，白名单/配额列为未来扩展。

## 11. 错误处理

| 场景 | 处理 |
| --- | --- |
| `FEISHU_APP_ID/SECRET` 缺失或无效 | 启动即报错退出，提示配置位置 |
| 长连接断开 | SDK 自动重连，日志提示 |
| 发消息失败（频控/网络） | 指数退避重试 3 次；仍失败则记日志跳过（transcript 已落盘不丢内容），下回合恢复 |
| PATCH 消息失败 | 降级为直接发送新消息 |
| 提交回答时 `busy` | 回复"面试官思考中，请稍候" |
| 事件重推 | 按 `event_id` 去重（LRU 近期 1000 条） |
| 评估失败 / 报告缺失 | 发错误提示；transcript 完好，可重新结束生成 |
| LLM 错误 | 沿用 runner 现有重试与 `error` 事件路径，泵转发为用户可见消息 |

## 12. 配置（`.env.example` 同步更新）

```bash
DEEPSEEK_API_KEY=sk-你的密钥        # 沿用：服务器统一密钥
# 飞书渠道（新增，启用飞书入口时必填）
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=你的应用密钥
INTERVIEW_SESSION_IDLE_TIMEOUT=1800 # 沿用，可选
```

启动：`python -m interview_agent.feishu`（与 Web 入口互不依赖，可同时运行）。

开放平台侧手动配置（README 详述）：创建企业自建应用 → 添加机器人能力 → 申请权限 `im:message`（接收）与 `im:message:send_as_bot`（发送）→ 订阅事件 `im.message.receive_v1` 并选择长连接模式 → 发布版本、可用范围设为全员。

## 13. 测试

全部使用 mock `FeishuClient` 与假 LLM，不访问真实飞书 API：

- **单元**：
  - `OnboardingFlow`：正常全填 / 各项跳过 / 简历多条拼接与 `END` / 取消 / 非法输入分支。
  - `FeishuBot` 分发路由：四语境（命令/引导/面试/空闲）× 输入类型矩阵；去重；p2p 过滤；单活跃会话约束；`busy` 提示。
  - 事件泵：按序注入事件（thinking→delta…→turn_end→…→done / error / 无 thinking 前导的开场 / 空闲超时 `task.ended`），断言 `send_text` / `patch_text` / `send_markdown_card` 调用序列与内容；PATCH 失败降级。
  - 配置加载：飞书凭证存在/缺失。
- **集成**：假 LLM 驱动 `SessionManager` 真实跑一场（开始→两回合→结束→报告卡片），两用户并发互不串。
- **手动验收**：真实飞书应用完整一场面试（含跳过各项、长简历分多条、报告卡片、空闲超时通知各一次）。

## 14. 未来扩展点（仅预留，不实现）

1. **语音消息面试**：入站规范化层加 `audio` 分支（下载音频 → ASR → 文本），出站渲染器加 TTS（回合文本 → 音频消息）；Agent 核心仍纯文本。本次的两处接口边界即为它而设。
2. **用户白名单 / 配额**：`open_id` 白名单环境变量，控制费用。
3. **群聊面试**：@机器人触发，会话路由升为 `(chat_id, user_id)` 维度。
4. **飞书内历史与面经库**：`历史` 命令列出会话、回发报告卡片；面经库 CRUD 走对话或卡片表单。
5. **Web 端实时语音/视频面试**（背景结论，属 Web 渠道）：浏览器 `getUserMedia` + WebSocket 音频上行 + VAD 断句 + 流式 ASR/TTS + 打断处理；现有 `delta` 事件协议与 `run_turn(on_delta)` 回调已为此就绪，"视频"部分（候选人录制留档 / 面试官头像）是表现层增量。该路线不走飞书（开放平台不向机器人开放会议实时音频流）。

## 15. 依赖变更

新增主依赖：`lark-oapi>=1.4`（官方 SDK：WS 长连接客户端 + 消息 API 客户端）。
