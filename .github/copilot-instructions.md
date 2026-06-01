# World Monitor · Free News MCP

This project has a **skill file** at `.github/skills/world-monitor-free-news/SKILL.md` that defines the workflow for daily news briefs, email sending, and news tool usage.

**Always read the SKILL.md first** before working on news-related tasks, especially:

- Creating or sending daily news briefs
- Searching or summarizing news
- Sending email digests
- Editing the email template
- Managing RSS sources

Key rules from the skill:
- Use `daily_brief` to fetch materials, then `send_email` with `body_format=plain`
- Base summaries strictly on tool-returned fields
- Preserve source links
- Do not invent facts
