#!/usr/bin/env python3
"""Standalone daily news brief — no MCP, no agent, no VS Code needed.

Can run anywhere Python 3 is available: GitHub Actions, cron, Task Scheduler,
a cloud server, or a friend's computer.

Usage:
    python scripts/daily_brief_standalone.py

Environment variables required (or in .env):
    NEWSAPI_KEY=...
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USERNAME=...
    SMTP_PASSWORD=...
    EMAIL_FROM=...
    EMAIL_TO=...
"""

import os
import sys

# Ensure we can import from the repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env if present (safe fallback; GitHub Actions uses real env vars)
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.isfile(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# Import functions from the existing MCP server (stdlib-only, safe)
from news_mcp_server import (
    collect_headlines,
    render_email_template,
    utc_now,
    isoformat,
    tool_send_email,
)


def build_plain_text_brief(sections: dict) -> str:
    """Format fetched articles into the plain-text shape the email converter
    recognises (section headers + numbered cards)."""

    label_map = {
        "world": ("国际", "🌍"),
        "domestic": ("国内", "🇨🇳"),
        "technology": ("科技", "💻"),
        "business": ("财经", "💰"),
    }

    lines = [
        f"基于 free-news MCP 于 {isoformat(utc_now())} 自动获取的材料整理。"
        "只使用工具返回的标题、描述、来源、发布时间和链接。"
    ]

    for cat_key in ["world", "domestic", "technology", "business"]:
        articles = sections.get(cat_key, [])
        if not articles:
            continue
        zh_label, _ = label_map.get(cat_key, (cat_key, ""))
        lines.append("")
        lines.append(zh_label)

        for idx, item in enumerate(articles, 1):
            title = item.get("title", "(无标题)")
            desc = item.get("description") or item.get("content_preview") or ""
            source = item.get("source", "未知来源")
            author = item.get("author")
            published = (item.get("published_at") or "").replace("T", " ").replace("Z", "")
            url = item.get("url", "")

            lines.append(f"{idx}. {title}")
            if desc:
                lines.append(f"{desc}")
            meta = f"来源：{source}"
            if author:
                meta += f" · {author}"
            if published:
                meta += f" · {published}"
            lines.append(meta)
            lines.append(f"链接：{url}")

    return "\n".join(lines)


def main():
    print("=" * 50)
    print("World Monitor · 每日早报")
    print(f"Time: {isoformat(utc_now())}")
    print("=" * 50)

    # ── 1. Fetch news for all 4 categories ──
    categories = ["world", "domestic", "technology", "business"]
    sections = {}
    all_warnings = []

    for cat in categories:
        print(f"\n📡 Fetching {cat} …")
        articles, warnings = collect_headlines(
            category=cat,
            country="cn",
            page_size=3,
            include_rss=True,
        )
        sections[cat] = articles
        all_warnings.extend(warnings)
        print(f"   → {len(articles)} articles")

    if all_warnings:
        print(f"\n⚠️  Warnings ({len(all_warnings)}):")
        for w in all_warnings:
            print(f"   • {w}")

    # ── 2. Build email body ──
    body = build_plain_text_brief(sections)
    print(f"\n📝 Email body length: {len(body)} chars")

    # ── 3. Send email ──
    print("\n📧 Sending email …")
    result = tool_send_email({
        "subject": "每日早报 · " + utc_now().strftime("%Y年%m月%d日"),
        "body": body,
        "body_format": "plain",
    })
    print(f"   ✅ Sent! Recipients: {result.get('recipients', '?')}")


if __name__ == "__main__":
    main()
