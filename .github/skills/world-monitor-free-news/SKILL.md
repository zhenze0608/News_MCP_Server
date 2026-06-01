---
name: world-monitor-free-news
description: "Use this skill when working with the World Monitor free-news MCP project: creating sourced daily news briefs, searching or summarizing NewsAPI/RSS articles, sending digest emails, adjusting the news email template, managing RSS sources, or validating this repository's MCP server."
---

# World Monitor Free News

## Project Orientation

Treat the repository root as the directory containing `news_mcp_server.py`. The project exposes a local stdio MCP server named `free-news` that gathers news from NewsAPI and configured RSS feeds, then lets the agent summarize and optionally email the results.

Important files:

- `news_mcp_server.py`: MCP server, tool implementations, plain-text-to-email HTML conversion, email rendering.
- `templates/news_brief_email.html`: default email shell with the dark header and newsletter body placeholder.
- `config/rss_sources.json`: RSS source list and category/region metadata.
- `.env.example`: required environment variables; never expose real `.env` secrets.
- `scripts/smoke_test.py`: no-network MCP smoke test.
- `scripts/live_test.py`: live NewsAPI/RSS test; use only when network/quota use is acceptable.
- `examples/mcp.vscode.example.json`: sample MCP client config.

## Core Rules

- Base summaries only on tool-returned article fields: `title`, `description`, `content_preview`, `source`, `author`, `published_at`, and `url`.
- Preserve source links. If a link is missing, say it is unavailable instead of inventing one.
- Do not add outside facts unless the user explicitly asks for separate research.
- For current/latest news, call the MCP tool; do not rely on model memory.
- If `domestic` returns non-domestic RSS items, keep the returned material and clearly note the mismatch. Do not replace it with invented domestic news.
- Do not print real email addresses, SMTP passwords, NewsAPI keys, or `.env` contents. The `send_email` tool returns masked recipients; keep them masked.

## Daily Brief Workflow

Use `daily_brief` for the standard daily morning brief.

Recommended arguments for a Chinese four-section brief:

```json
{
  "categories": ["international", "domestic", "technology", "business"],
  "country": "cn",
  "items_per_category": 3,
  "output_language": "zh"
}
```

Map returned sections to user-facing Chinese labels:

- `world` -> `国际`
- `domestic` -> `国内`
- `technology` -> `科技`
- `business` -> `财经`

For each item, include:

- A concise title, translated if useful.
- A short summary based only on `description` or `content_preview`.
- Source metadata: source, optional author, published time.
- The original URL.

If the user asks to send the brief by email, call `send_email` after composing the brief.

## Email Digest Formatting

Prefer `body_format=plain` unless the user asks for custom HTML. The server converts structured plain text into the project newsletter style when the current `news_mcp_server.py` is running.

Use this plain-text shape to trigger section headers and news cards:

```text
基于 free-news MCP 于 2026-06-01T12:28:57+00:00 返回的材料整理。只使用工具返回的标题、描述、来源、发布时间和链接。
注意：工具的 domestic/cn 板块本次返回的是 China News World 的国际类条目，以下按工具结果保留。

国际
1. 加纳通过刑事化 LGBTQ+ 活动的广泛法案
材料称，加纳议会通过一项法案，涉及刑事化 LGBTQ+ 相关活动和身份认同。
来源：The Guardian World · John Musenze · 2026-06-01 11:59 UTC
链接：https://www.theguardian.com/global-development/2026/jun/01/ghana-new-law-criminalising-lgbtq-activity

国内
1. 韩国韩华航空航天公司发生爆炸致 5 死 2 伤
中国新闻网材料称，当地时间 6 月 1 日，韩国大田的韩华航空航天公司发生爆炸事故。
来源：China News World · 2026-06-01 11:58 UTC
链接：https://www.chinanews.com.cn/gj/2026/06-01/10632397.shtml
```

The card converter recognizes these section headings: `国际`, `国内`, `科技`, `财经`, plus English aliases such as `world`, `domestic`, `technology`, and `business`. Numbered items become cards. `来源：` becomes card metadata, and `链接：` becomes the title/read link.

Use `body_format=html` only when exact one-off markup is needed, such as testing a custom card layout while the MCP server process has not been restarted.

## Template Editing

The default email shell is `templates/news_brief_email.html`.

Keep these placeholders intact:

- `{{subject}}`
- `{{preheader}}`
- `{{generated_at}}`
- `{{body_html}}`
- `{{footer_note}}`

The intended visual style is:

- Dark navy top header.
- White title text in the header.
- Light gray-blue metadata text.
- Section headings with icon plus dark divider line.
- Each news item as a pale card with a dark left border.
- Source/read-link metadata at the bottom of each card.

Template file edits are read when `send_email` renders a message. Python logic changes in `news_mcp_server.py` require restarting the MCP server process before existing clients see them.

## Other Tool Patterns

Use these tools for common requests:

- `get_headlines`: current headlines by category/country/source.
- `get_news_by_category`: category browsing wrapper.
- `search_news`: keyword/topic search.
- `summarize_news`: collect articles for a topic and return a Copilot summarization prompt.
- `news_timeline`: group recent topic articles by date.
- `trending_topics`: estimate repeated terms from headlines.
- `list_rss_sources`: inspect configured RSS sources.
- `set_preferences` and `get_my_feed`: save and use local personalized feeds.
- `send_email`: send a composed brief to the configured recipient.

## Validation

Before finishing code or template changes, run:

```powershell
python -m py_compile news_mcp_server.py
python scripts\smoke_test.py
git diff --check
```

Run `scripts\live_test.py` only when the user accepts live network calls and quota usage.

When modifying email rendering, also test `render_email_template(...)` or send a real test email if the user asks for an end-to-end check.

## Safety Notes

- Never commit `.env`, API keys, SMTP passwords, or private recipient addresses.
- Prefer `.env.example` for public configuration examples.
- Do not silently rewrite or relabel RSS categories. If RSS category behavior is surprising, report what the tool returned.
- If `send_email` fails due to missing SMTP config, tell the user which environment variable is missing without revealing secret values.
