# 免费新闻 MCP - World Monitor

本项目是一个本地 stdio MCP 服务，用 **NewsAPI + RSS** 给 Copilot、Codex 或其他 MCP 客户端提供新闻工具。

特点：

- 使用 Python 标准库，不额外安装包。
- NewsAPI 做主数据源，RSS 做补充和兜底。
- AI 摘要交给 agent 完成，MCP 只负责提供材料和提示词。
- NewsAPI key、邮箱地址、SMTP 密码都放在 `.env`，不会进入 Git。
- 支持把重要早报或专题总结发送到邮箱。

## 快速开始

```powershell
cd <PROJECT_DIR>
conda activate py38
copy .env.example .env
notepad .env
```

把你的 NewsAPI key 写进 `.env`：

```env
NEWSAPI_KEY=your_newsapi_key_here
NEWS_MCP_DEFAULT_COUNTRY=us
NEWS_MCP_DEFAULT_LANGUAGE=en
```

`.env` 已经被 `.gitignore` 忽略。不要把真实 key、邮箱授权码、私人路径写进 README 或示例配置。

## 本地测试

冒烟测试不会联网，也不会消耗 NewsAPI 额度：

```powershell
conda activate py38
python scripts\smoke_test.py
```

真实联网测试会调用 NewsAPI 和 RSS：

```powershell
conda activate py38
python scripts\live_test.py
```

## MCP 客户端配置

推荐把真实本机路径只放在本地 `.vscode/mcp.json` 里。这个目录已经加入 `.gitignore`，不会再提交到 Git。

示例见：

```text
examples/mcp.vscode.example.json
```

公开示例里请使用占位符，不要写你的真实设备路径：

```json
{
  "servers": {
    "free-news": {
      "type": "stdio",
      "command": "conda",
      "args": ["run", "-n", "py38", "python", "<PROJECT_DIR>\\news_mcp_server.py"],
      "cwd": "<PROJECT_DIR>"
    }
  }
}
```

你本机的 `.vscode/mcp.json` 可以继续使用真实路径，例如：

```json
{
  "servers": {
    "free-news": {
      "type": "stdio",
      "command": "conda",
      "args": ["run", "-n", "py38", "python", "D:\\user\\mcp\\world_monitor\\news_mcp_server.py"],
      "cwd": "D:\\user\\mcp\\world_monitor"
    }
  }
}
```

## 工具列表

| 工具 | 说明 |
|---|---|
| `get_headlines` | 获取头条新闻，支持分类、国家和数量 |
| `search_news` | 按关键词搜索新闻 |
| `get_news_by_category` | 按类别浏览新闻 |
| `get_top_stories` | 跨类别聚合重要新闻 |
| `summarize_news` | 搜索指定话题，并返回给 agent 的总结提示词 |
| `daily_brief` | 生成每日早报材料和中文简报提示词 |
| `trending_topics` | 从头条中粗略提取热点词 |
| `news_timeline` | 按日期整理某个事件的相关新闻 |
| `set_preferences` | 保存本地偏好 |
| `get_my_feed` | 根据偏好生成个人新闻流 |
| `zh_to_en` / `en_to_zh` | 返回翻译提示词，让 agent 翻译 |
| `list_rss_sources` | 列出当前 RSS 源和配置位置 |
| `send_email` | 把早报、话题总结或其他正文发送到 `.env` 配置的邮箱 |

## 给 Agent 的常用指令

### 每日早报

```text
用 free-news MCP 给我今天的每日早报：国际、国内、科技、财经各 3 条。请只基于工具返回的新闻材料总结，保留来源链接，不要编造额外事实。
```

推荐工具调用：

```json
{
  "name": "daily_brief",
  "arguments": {
    "country": "us",
    "categories": ["world", "domestic", "technology", "business"],
    "items_per_category": 3,
    "output_language": "zh"
  }
}
```

### 查询某个话题

```text
用 free-news MCP 搜索最近 7 天关于 Taiwan Strait 的新闻。调用 summarize_news，topic=Taiwan Strait，language=en，days_back=7，max_articles=12。请用中文总结来龙去脉、关键进展和后续看点，并列出来源链接。
```

### AI 行业时间线

```text
用 free-news MCP 总结最近 14 天 AI 行业大事。调用 news_timeline，topic=artificial intelligence OR OpenAI OR Nvidia，language=en，days_back=14。请按时间线输出中文摘要，并标注每条信息来源。
```

### 热点趋势

```text
用 free-news MCP 调用 trending_topics，category=world，max_topics=10。告诉我今天国际新闻里反复出现的热点词、对应新闻和为什么值得关注。
```

### 设置个人偏好

```text
用 free-news MCP 调用 set_preferences。topics=["AI", "Taiwan Strait", "semiconductors", "US China"]，categories=["world", "technology", "business"]，countries=["us"]，languages=["en"]。
```

之后可以直接说：

```text
用 free-news MCP 调用 get_my_feed。根据我的偏好生成今天的个人新闻流，并用中文总结。
```

### 发送到邮箱

```text
把刚才这份新闻简报发送到我的默认邮箱。调用 free-news MCP 的 send_email，subject=今日早报，body=刚才整理好的完整正文，body_format=plain。不要在对话里显示我的邮箱地址。
```

推荐工具调用：

```json
{
  "name": "send_email",
  "arguments": {
    "subject": "今日早报",
    "body": "这里放刚才整理好的完整新闻简报正文",
    "body_format": "plain"
  }
}
```

## 邮件排版模板

邮件默认使用固定模板：

```text
templates/news_brief_email.html
```

日常让 agent 发送邮件时，建议使用 `body_format=plain`，只把正文传给 MCP。服务会自动把正文转换成统一 HTML 邮件，保证标题、边距、字体、链接、页脚风格一致。

如果你想换模板，可以复制一份 HTML 文件，然后在 `.env` 里设置：

```env
EMAIL_TEMPLATE_PATH=templates/news_brief_email.html
```

模板支持这些占位符：

- `{{subject}}`
- `{{preheader}}`
- `{{generated_at}}`
- `{{body_html}}`
- `{{footer_note}}`

## Gmail 邮箱配置

如果你用 Gmail，推荐使用 587 + STARTTLS：

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_gmail@gmail.com
SMTP_PASSWORD=your_google_app_password
SMTP_USE_TLS=true
SMTP_USE_SSL=false
EMAIL_FROM=your_gmail@gmail.com
EMAIL_TO=your_private_inbox@gmail.com
```

也可以使用 465 + SSL：

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your_gmail@gmail.com
SMTP_PASSWORD=your_google_app_password
SMTP_USE_TLS=false
SMTP_USE_SSL=true
EMAIL_FROM=your_gmail@gmail.com
EMAIL_TO=your_private_inbox@gmail.com
```

注意：

- `SMTP_PASSWORD` 通常不是你的 Gmail 登录密码，而是 Google 生成的应用专用密码。
- Google 应用专用密码通常要求账号开启两步验证。
- 生成的应用专用密码常显示为 16 位字符，复制到 `.env` 时建议去掉空格。
- 如果你是学校、公司或 Workspace 账号，应用专用密码入口可能被管理员关闭。

测试邮件指令：

```text
调用 free-news MCP 的 send_email，subject=新闻 MCP 测试，body=这是一封测试邮件，body_format=plain。
```

## RSS 源管理

RSS 配置文件：

```text
config/rss_sources.json
```

新增源时添加一个对象：

```json
{
  "name": "Example Tech",
  "url": "https://example.com/rss.xml",
  "category": "technology",
  "language": "en",
  "region": "world",
  "enabled": true,
  "weight": 0.8
}
```

改完 RSS 配置后，重启 MCP 服务即可生效。

## 免费额度提醒

NewsAPI 免费档通常只有 100 requests/day。`daily_brief`、`get_top_stories` 这类跨类别工具会多次调用 NewsAPI。日常建议：

- 每日早报每天跑 1 次。
- 话题总结尽量一次问清楚关键词和时间范围。
- RSS 不消耗 NewsAPI 次数，可以把常看的站点加进 RSS 兜底。
