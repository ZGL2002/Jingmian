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
