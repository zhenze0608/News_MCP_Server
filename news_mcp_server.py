#!/usr/bin/env python3
"""A small stdio MCP server for NewsAPI + RSS news feeds.

The server intentionally uses only Python's standard library so it can run in a
plain conda environment without installing extra packages.
"""

from __future__ import annotations

import datetime as dt
import email.utils
from email.message import EmailMessage
import html
import json
import os
from pathlib import Path
import re
import smtplib
import sys
import traceback
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent
NEWSAPI_BASE = "https://newsapi.org/v2"
SERVER_NAME = "free-news-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

NEWSAPI_CATEGORIES = {
    "business",
    "entertainment",
    "general",
    "health",
    "science",
    "sports",
    "technology",
}

CATEGORY_ALIASES = {
    "ai": "technology",
    "artificial intelligence": "technology",
    "business": "business",
    "财经": "business",
    "finance": "business",
    "financial": "business",
    "国内": "domestic",
    "china": "domestic",
    "domestic": "domestic",
    "entertainment": "entertainment",
    "娱乐": "entertainment",
    "general": "general",
    "headline": "general",
    "headlines": "general",
    "health": "health",
    "健康": "health",
    "international": "world",
    "world": "world",
    "global": "world",
    "国际": "world",
    "science": "science",
    "科学": "science",
    "sport": "sports",
    "sports": "sports",
    "体育": "sports",
    "tech": "technology",
    "technology": "technology",
    "科技": "technology",
}

DEFAULT_DAILY_CATEGORIES = ["world", "domestic", "technology", "business"]
DEFAULT_RSS_CONFIG = ROOT / "config" / "rss_sources.json"
DEFAULT_PREFS_PATH = ROOT / ".news_mcp_preferences.json"
DEFAULT_EMAIL_TEMPLATE = ROOT / "templates" / "news_brief_email.html"

EN_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "also",
    "amid",
    "among",
    "around",
    "because",
    "before",
    "being",
    "could",
    "from",
    "have",
    "into",
    "more",
    "news",
    "over",
    "says",
    "than",
    "that",
    "their",
    "there",
    "this",
    "with",
    "will",
    "would",
    "your",
}


class McpError(Exception):
    def __init__(self, message: str, code: int = -32000, data: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.data = data


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"failed to read json {path}: {exc}")
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def log(message: str) -> None:
    print(f"[{SERVER_NAME}] {message}", file=sys.stderr, flush=True)


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    if not value:
        return default
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except Exception:
        return path.name


def default_country() -> str:
    return os.environ.get("NEWS_MCP_DEFAULT_COUNTRY", "us").strip() or "us"


def default_language() -> str:
    return os.environ.get("NEWS_MCP_DEFAULT_LANGUAGE", "en").strip() or "en"


def rss_config_path() -> Path:
    return env_path("NEWS_MCP_RSS_CONFIG", DEFAULT_RSS_CONFIG)


def prefs_path() -> Path:
    return env_path("NEWS_MCP_PREFERENCES", DEFAULT_PREFS_PATH)


def email_template_path() -> Path:
    return env_path("EMAIL_TEMPLATE_PATH", DEFAULT_EMAIL_TEMPLATE)


def normalize_category(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    value = str(category).strip()
    if not value:
        return None
    return CATEGORY_ALIASES.get(value.lower(), value.lower())


def clean_text(value: Optional[str]) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_datetime(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def isoformat(value: Optional[dt.datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def article_sort_key(article: Dict[str, Any]) -> dt.datetime:
    parsed = parse_datetime(article.get("published_at"))
    if parsed is None:
        return dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    return parsed


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = re.split(r"[,;\n]+", value)
        return [part.strip() for part in parts if part.strip()]
    return [str(value).strip()]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def clamp_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(min_value, min(max_value, parsed))


def newsapi_key() -> Optional[str]:
    key = os.environ.get("NEWSAPI_KEY", "").strip()
    if not key or key == "your_newsapi_key_here":
        return None
    return key


def mask_email(address: str) -> str:
    address = address.strip()
    if "@" not in address:
        return "***"
    local, domain = address.split("@", 1)
    if len(local) <= 2:
        local_mask = local[:1] + "***"
    else:
        local_mask = local[:1] + "***" + local[-1:]
    return f"{local_mask}@{domain}"


def html_linkify(escaped_text: str) -> str:
    return re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" style="color:#1a73e8;text-decoration:none;">\1</a>',
        escaped_text,
    )


NEWS_EMAIL_SECTION_ICONS = {
    "国际": "🌍",
    "国内": "🏠",
    "科技": "💻",
    "财经": "💰",
}


def normalize_news_email_section(line: str) -> Optional[str]:
    value = line.strip()
    value = re.sub(r"^#+\s*", "", value)
    value = re.sub(r"^\*\*(.*?)\*\*$", r"\1", value).strip()
    value = value.strip(" -*:：")
    lower = value.lower()
    aliases = {
        "world": "国际",
        "international": "国际",
        "global": "国际",
        "domestic": "国内",
        "china": "国内",
        "technology": "科技",
        "tech": "科技",
        "business": "财经",
        "finance": "财经",
        "financial": "财经",
    }
    if lower in aliases:
        return aliases[lower]
    for section in NEWS_EMAIL_SECTION_ICONS:
        if value == section or value.startswith(f"{section}（") or value.startswith(f"{section}("):
            return section
    return None


def parse_news_email_brief(text: str) -> Optional[Dict[str, Any]]:
    intro: List[str] = []
    sections: List[Dict[str, Any]] = []
    current_section: Optional[Dict[str, Any]] = None
    current_item: Optional[Dict[str, Any]] = None

    def finish_item() -> None:
        nonlocal current_item
        if not current_item or not current_section:
            current_item = None
            return
        summary_parts = current_item.pop("summary_parts", [])
        current_item["summary"] = " ".join(summary_parts).strip()
        if current_item.get("title"):
            current_section["items"].append(current_item)
        current_item = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        section = normalize_news_email_section(line)
        if section:
            finish_item()
            current_section = {"title": section, "items": []}
            sections.append(current_section)
            continue

        item_match = re.match(r"^(\d+)[\.、]\s*(.+)$", line)
        if item_match and current_section is not None:
            finish_item()
            current_item = {
                "title": item_match.group(2).strip(),
                "summary_parts": [],
                "source": "",
                "url": "",
            }
            continue

        if current_item is not None:
            if re.match(r"^来源[:：]", line):
                current_item["source"] = re.sub(r"^来源[:：]\s*", "", line).strip()
            elif re.match(r"^链接[:：]", line):
                current_item["url"] = re.sub(r"^链接[:：]\s*", "", line).strip()
            else:
                current_item["summary_parts"].append(line)
        elif current_section is None:
            intro.append(line)

    finish_item()
    sections = [section for section in sections if section["items"]]
    if not sections:
        return None
    return {"intro": intro, "sections": sections}


def render_news_email_brief_html(parsed: Dict[str, Any]) -> str:
    parts: List[str] = []
    for note in parsed.get("intro", []):
        content = html_linkify(html.escape(note))
        parts.append(
            '<p style="margin:0 0 10px;font-size:14px;line-height:1.7;color:#627d98;">'
            f"{content}</p>"
        )

    for index, section in enumerate(parsed.get("sections", [])):
        title = str(section.get("title") or "").strip()
        icon = NEWS_EMAIL_SECTION_ICONS.get(title, "•")
        margin_top = "26px" if index or parts else "12px"
        parts.append(
            f'<h2 style="font-size:20px;line-height:1.35;margin:{margin_top} 0 16px;'
            'padding-bottom:10px;border-bottom:2px solid #102a43;color:#102a43;font-weight:700;">'
            f"{html.escape(icon)} {html.escape(title)}</h2>"
        )

        for item in section.get("items", []):
            title_text = html.escape(str(item.get("title") or "Untitled").strip())
            url = str(item.get("url") or "").strip()
            href = html.escape(url, quote=True)
            if url:
                title_html = (
                    f'<a href="{href}" style="color:#102a43;text-decoration:none;">'
                    f"{title_text}</a>"
                )
            else:
                title_html = title_text

            summary = html_linkify(html.escape(str(item.get("summary") or "").strip()))
            source = html.escape(str(item.get("source") or "").strip())
            meta_parts = []
            if source:
                meta_parts.append(source)
            if url:
                meta_parts.append(
                    f'<a href="{href}" style="color:#3e4c59;text-decoration:none;">阅读原文 ↗</a>'
                )
            meta_html = " · ".join(meta_parts)

            parts.append(
                '<div style="margin:14px 0 18px;padding:18px 18px;background:#f8fafc;'
                'border-left:3px solid #486581;border-radius:6px;">'
                f'<div style="margin:0 0 10px;font-size:16px;line-height:1.45;'
                f'font-weight:700;color:#102a43;">{title_html}</div>'
                f'<div style="margin:0 0 12px;font-size:15px;line-height:1.65;'
                f'color:#486581;">{summary}</div>'
                f'<div style="font-size:13px;line-height:1.5;color:#627d98;">{meta_html}</div>'
                '</div>'
            )

    return "\n".join(parts)


def text_to_email_html(text: str) -> str:
    structured = parse_news_email_brief(text)
    if structured:
        return render_news_email_brief_html(structured)

    parts: List[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            close_list()
            parts.append('<div style="height:10px;line-height:10px;">&nbsp;</div>')
            continue

        if line.startswith("### "):
            close_list()
            content = html_linkify(html.escape(line[4:].strip()))
            parts.append(f'<h3 style="font-size:16px;line-height:1.45;margin:22px 0 8px;color:#102a43;font-weight:700;">{content}</h3>')
        elif line.startswith("## "):
            close_list()
            content = html_linkify(html.escape(line[3:].strip()))
            parts.append(f'<h2 style="font-size:20px;line-height:1.35;margin:28px 0 16px;padding-bottom:10px;border-bottom:2px solid #102a43;color:#102a43;font-weight:700;">{content}</h2>')
        elif line.startswith("# "):
            close_list()
            content = html_linkify(html.escape(line[2:].strip()))
            parts.append(f'<h2 style="font-size:22px;line-height:1.35;margin:0 0 14px;color:#102a43;font-weight:700;">{content}</h2>')
        elif re.match(r"^[-*]\s+", line):
            if not in_list:
                parts.append('<ul style="margin:8px 0 14px 22px;padding:0;">')
                in_list = True
            content = html_linkify(html.escape(re.sub(r"^[-*]\s+", "", line)))
            parts.append(f'<li style="margin:6px 0;line-height:1.68;">{content}</li>')
        else:
            close_list()
            content = html_linkify(html.escape(line))
            parts.append(f'<p style="margin:0 0 12px;line-height:1.72;color:#486581;">{content}</p>')

    close_list()
    return "\n".join(parts)


def render_email_template(subject: str, body: str, body_format: str) -> Tuple[str, str]:
    template_path = email_template_path()
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        template = (
            "<html><body><h1>{{subject}}</h1>"
            "<div>{{body_html}}</div><hr><p>{{footer_note}}</p></body></html>"
        )

    body_html = body if body_format == "html" else text_to_email_html(body)
    now = utc_now()
    generated_at = now.strftime("%Y年%m月%d日")
    preheader = clean_text(body)[:120] or subject
    footer_note = (
        "本邮件由 World Monitor · Free News MCP 自动生成。"
        f"数据来源于 NewsAPI 及配置的 RSS 源，仅供个人参考。生成时间：{isoformat(now)}"
    )
    replacements = {
        "subject": html.escape(subject),
        "preheader": html.escape(preheader),
        "generated_at": html.escape(generated_at),
        "body_html": body_html,
        "footer_note": html.escape(footer_note),
    }
    for key, value in replacements.items():
        template = template.replace("{{" + key + "}}", value)
    return template, display_path(template_path)


def http_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 12,
) -> Tuple[Optional[bytes], Optional[str]]:
    query = ""
    if params:
        cleaned = {
            key: value
            for key, value in params.items()
            if value is not None and value != "" and value != []
        }
        query = urllib.parse.urlencode(cleaned, doseq=True)
    full_url = f"{url}?{query}" if query else url
    req_headers = {
        "User-Agent": f"{SERVER_NAME}/{SERVER_VERSION} (+local MCP)",
    }
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(full_url, headers=req_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return None, f"HTTP {exc.code} from {url}: {body[:300]}"
    except Exception as exc:
        return None, f"{type(exc).__name__} while fetching {url}: {exc}"


def http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 12,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    body, error = http_get(url, params=params, headers=headers, timeout=timeout)
    if error:
        return None, error
    assert body is not None
    try:
        return json.loads(body.decode("utf-8")), None
    except Exception as exc:
        return None, f"JSON parse error from {url}: {exc}"


def normalize_newsapi_article(raw: Dict[str, Any], category: Optional[str]) -> Dict[str, Any]:
    source = raw.get("source") or {}
    published = parse_datetime(raw.get("publishedAt"))
    return {
        "title": clean_text(raw.get("title")),
        "description": clean_text(raw.get("description")),
        "content_preview": clean_text(raw.get("content")),
        "url": raw.get("url"),
        "image_url": raw.get("urlToImage"),
        "published_at": isoformat(published),
        "source": source.get("name") or source.get("id") or "NewsAPI",
        "author": raw.get("author"),
        "category": category,
        "language": None,
        "origin": "newsapi",
    }


def fetch_newsapi(
    endpoint: str,
    params: Dict[str, Any],
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    key = newsapi_key()
    if not key:
        return [], ["NEWSAPI_KEY is not configured; using RSS-only fallback."]

    url = f"{NEWSAPI_BASE}/{endpoint.lstrip('/')}"
    params = dict(params)
    params["pageSize"] = clamp_int(params.get("pageSize"), 10, 1, 100)
    headers = {"X-Api-Key": key}
    payload, error = http_get_json(url, params=params, headers=headers)
    if error:
        return [], [error]
    if not payload:
        return [], [f"Empty NewsAPI response from {endpoint}."]
    if payload.get("status") != "ok":
        message = payload.get("message") or payload.get("code") or payload
        return [], [f"NewsAPI error: {message}"]
    articles = [
        normalize_newsapi_article(item, category)
        for item in payload.get("articles", [])
        if item.get("title") and item.get("url")
    ]
    return articles, warnings


def load_rss_config() -> Dict[str, Any]:
    return read_json(rss_config_path(), {"defaults": {}, "sources": []})


def enabled_rss_sources(category: Optional[str] = None) -> List[Dict[str, Any]]:
    config = load_rss_config()
    wanted = normalize_category(category)
    sources: List[Dict[str, Any]] = []
    for source in config.get("sources", []):
        if source.get("enabled", True) is False:
            continue
        source_category = normalize_category(source.get("category")) or "general"
        if wanted and wanted not in ("general", source_category):
            if not (wanted == "domestic" and source.get("region") == "cn"):
                continue
        sources.append(source)
    return sources


def xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def first_child_text(element: ET.Element, names: Sequence[str]) -> str:
    wanted = set(names)
    for child in list(element):
        if xml_local_name(child.tag) in wanted:
            return clean_text("".join(child.itertext()))
    return ""


def atom_or_rss_link(element: ET.Element) -> str:
    for child in list(element):
        if xml_local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        if href:
            return href.strip()
        text = clean_text("".join(child.itertext()))
        if text:
            return text
    return ""


def parse_rss_items(body: bytes, source: Dict[str, Any], max_items: int) -> List[Dict[str, Any]]:
    root = ET.fromstring(body)
    items = [node for node in root.iter() if xml_local_name(node.tag) in ("item", "entry")]
    articles: List[Dict[str, Any]] = []
    for item in items[:max_items]:
        title = first_child_text(item, ["title"])
        link = atom_or_rss_link(item)
        description = first_child_text(item, ["description", "summary", "subtitle", "content"])
        published_text = first_child_text(item, ["pubDate", "published", "updated", "date"])
        published = parse_datetime(published_text)
        if not title or not link:
            continue
        articles.append(
            {
                "title": title,
                "description": description,
                "content_preview": description,
                "url": link,
                "image_url": None,
                "published_at": isoformat(published),
                "source": source.get("name") or "RSS",
                "author": first_child_text(item, ["creator", "author"]),
                "category": normalize_category(source.get("category")) or "general",
                "language": source.get("language"),
                "origin": "rss",
                "rss_region": source.get("region"),
            }
        )
    return articles


def fetch_rss(
    category: Optional[str] = None,
    query: Optional[str] = None,
    max_articles: int = 40,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    config = load_rss_config()
    defaults = config.get("defaults", {})
    timeout = clamp_int(defaults.get("request_timeout_seconds"), 12, 3, 30)
    per_feed = clamp_int(defaults.get("items_per_feed"), 8, 1, 30)
    sources = enabled_rss_sources(category)
    warnings: List[str] = []
    articles: List[Dict[str, Any]] = []
    query_lower = query.lower().strip() if query else ""

    for source in sources:
        body, error = http_get(source["url"], timeout=timeout)
        if error:
            warnings.append(f"{source.get('name', source.get('url'))}: {error}")
            continue
        if not body:
            continue
        try:
            parsed = parse_rss_items(body, source, per_feed)
        except Exception as exc:
            warnings.append(f"{source.get('name', source.get('url'))}: RSS parse error: {exc}")
            continue
        if query_lower:
            parsed = [
                item
                for item in parsed
                if query_lower in (
                    f"{item.get('title', '')} {item.get('description', '')} {item.get('source', '')}".lower()
                )
            ]
        articles.extend(parsed)
        if len(articles) >= max_articles:
            break
    return articles[:max_articles], warnings


def dedupe_articles(articles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    result: List[Dict[str, Any]] = []
    for article in articles:
        url = (article.get("url") or "").strip().lower()
        if url:
            parsed = urllib.parse.urlsplit(url)
            url_key = urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
            )
            key = f"url:{url_key}"
        else:
            key = f"title:{article.get('source', '')}:{article.get('title', '')}".lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def sorted_articles(articles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(dedupe_articles(articles), key=article_sort_key, reverse=True)


def newsapi_query_for_category(category: Optional[str]) -> Optional[str]:
    category = normalize_category(category)
    if category == "world":
        return 'world OR international OR global'
    if category == "domestic":
        return None
    return None


def collect_headlines(
    category: Optional[str],
    country: Optional[str],
    page_size: int,
    include_rss: bool,
    sources: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    category = normalize_category(category)
    country = country or default_country()
    page_size = clamp_int(page_size, 10, 1, 100)
    warnings: List[str] = []
    articles: List[Dict[str, Any]] = []

    newsapi_params: Dict[str, Any] = {"pageSize": page_size}
    endpoint = "top-headlines"
    if sources:
        newsapi_params["sources"] = ",".join(sources)
    elif category == "world":
        endpoint = "everything"
        newsapi_params.update(
            {
                "q": newsapi_query_for_category(category),
                "language": default_language(),
                "sortBy": "publishedAt",
            }
        )
    else:
        newsapi_params["country"] = country
        if category in NEWSAPI_CATEGORIES:
            newsapi_params["category"] = category

    fetched, newsapi_warnings = fetch_newsapi(endpoint, newsapi_params, category)
    articles.extend(fetched)
    warnings.extend(newsapi_warnings)

    if include_rss:
        rss_articles, rss_warnings = fetch_rss(category=category, max_articles=max(20, page_size * 2))
        articles.extend(rss_articles)
        warnings.extend(rss_warnings)

    return sorted_articles(articles)[:page_size], warnings


def collect_search(
    query: str,
    language: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    sort_by: str,
    page_size: int,
    include_rss: bool,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    query = str(query or "").strip()
    if not query:
        raise McpError("search_news requires a non-empty query.", code=-32602)
    page_size = clamp_int(page_size, 10, 1, 100)
    language = (language or default_language()).strip() or None
    sort_by = sort_by if sort_by in {"relevancy", "popularity", "publishedAt"} else "publishedAt"
    warnings: List[str] = []
    articles: List[Dict[str, Any]] = []

    params = {
        "q": query,
        "language": language,
        "from": from_date,
        "to": to_date,
        "sortBy": sort_by,
        "pageSize": page_size,
    }
    fetched, newsapi_warnings = fetch_newsapi("everything", params, category=None)
    articles.extend(fetched)
    warnings.extend(newsapi_warnings)

    if include_rss:
        rss_articles, rss_warnings = fetch_rss(query=query, max_articles=max(20, page_size * 2))
        articles.extend(rss_articles)
        warnings.extend(rss_warnings)

    return sorted_articles(articles)[:page_size], warnings


def article_lines(articles: Sequence[Dict[str, Any]], max_items: int = 20) -> str:
    lines = []
    for idx, item in enumerate(articles[:max_items], 1):
        title = item.get("title") or "(untitled)"
        source = item.get("source") or "unknown"
        published = item.get("published_at") or "unknown time"
        desc = item.get("description") or item.get("content_preview") or ""
        url = item.get("url") or ""
        lines.append(f"{idx}. [{source} | {published}] {title}\n   {desc}\n   {url}")
    return "\n".join(lines)


def summary_prompt(topic: str, articles: Sequence[Dict[str, Any]], language: str = "zh") -> str:
    output_language = "中文" if language.startswith("zh") else language
    return (
        f"请基于以下新闻材料，用{output_language}总结“{topic}”。\n"
        "要求：先用3-5句话讲清楚发生了什么、为什么重要、后续看点；"
        "再列出关键信息点；最后标注信息来源链接。不要编造材料中没有的事实。\n\n"
        f"{article_lines(articles)}"
    )


def daily_brief_prompt(sections: Dict[str, List[Dict[str, Any]]], language: str = "zh") -> str:
    output_language = "中文" if language.startswith("zh") else language
    chunks = []
    for category, articles in sections.items():
        chunks.append(f"## {category}\n{article_lines(articles, max_items=10)}")
    return (
        f"请用{output_language}把下面材料整理成一份每日早报。\n"
        "格式：一句话总览；然后按国际/国内/科技/财经等板块列出3-5条；"
        "每条包含标题、2句话摘要、为什么值得关注、来源链接。"
        "只基于材料，不要补充未经证实的细节。\n\n"
        + "\n\n".join(chunks)
    )


def make_result(
    tool: str,
    data: Dict[str, Any],
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    result = {"tool": tool, "generated_at": isoformat(utc_now())}
    result.update(data)
    if warnings:
        result["warnings"] = warnings
    return result


def tool_get_headlines(args: Dict[str, Any]) -> Dict[str, Any]:
    category = args.get("category")
    country = args.get("country")
    page_size = clamp_int(args.get("page_size"), 10, 1, 50)
    include_rss = bool(args.get("include_rss", True))
    sources = as_list(args.get("sources"))
    articles, warnings = collect_headlines(category, country, page_size, include_rss, sources=sources)
    return make_result(
        "get_headlines",
        {
            "category": normalize_category(category) or "general",
            "country": country or default_country(),
            "articles": articles,
        },
        warnings,
    )


def tool_search_news(args: Dict[str, Any]) -> Dict[str, Any]:
    articles, warnings = collect_search(
        query=args.get("query", ""),
        language=args.get("language"),
        from_date=args.get("from_date"),
        to_date=args.get("to_date"),
        sort_by=args.get("sort_by", "publishedAt"),
        page_size=clamp_int(args.get("page_size"), 10, 1, 50),
        include_rss=bool(args.get("include_rss", True)),
    )
    return make_result(
        "search_news",
        {"query": args.get("query", ""), "articles": articles},
        warnings,
    )


def tool_get_news_by_category(args: Dict[str, Any]) -> Dict[str, Any]:
    return tool_get_headlines(args)


def story_score(article: Dict[str, Any]) -> float:
    age_hours = max((utc_now() - article_sort_key(article)).total_seconds() / 3600, 0)
    recency = max(0.0, 48.0 - age_hours) / 48.0
    source_bonus = 0.15 if article.get("origin") == "newsapi" else 0.0
    text_len = len((article.get("title") or "") + " " + (article.get("description") or ""))
    substance = min(text_len / 240.0, 1.0) * 0.2
    return recency + source_bonus + substance


def tool_get_top_stories(args: Dict[str, Any]) -> Dict[str, Any]:
    categories = as_list(args.get("categories")) or ["world", "domestic", "technology", "business"]
    total = clamp_int(args.get("total"), 8, 1, 30)
    country = args.get("country") or default_country()
    warnings: List[str] = []
    pool: List[Dict[str, Any]] = []
    for category in categories:
        articles, sub_warnings = collect_headlines(category, country, 8, include_rss=True)
        pool.extend(articles)
        warnings.extend(sub_warnings)
    ranked = sorted(dedupe_articles(pool), key=story_score, reverse=True)[:total]
    return make_result(
        "get_top_stories",
        {
            "categories": [normalize_category(item) or item for item in categories],
            "country": country,
            "articles": ranked,
            "copilot_prompt": summary_prompt("today's top stories", ranked),
        },
        warnings,
    )


def tool_summarize_news(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = str(args.get("topic") or args.get("query") or "").strip()
    if not topic:
        raise McpError("summarize_news requires topic.", code=-32602)
    days_back = clamp_int(args.get("days_back"), 7, 1, 30)
    max_articles = clamp_int(args.get("max_articles"), 12, 1, 30)
    since = (utc_now() - dt.timedelta(days=days_back)).date().isoformat()
    articles, warnings = collect_search(
        query=topic,
        language=args.get("language"),
        from_date=since,
        to_date=None,
        sort_by="publishedAt",
        page_size=max_articles,
        include_rss=True,
    )
    return make_result(
        "summarize_news",
        {
            "topic": topic,
            "articles": articles,
            "copilot_prompt": summary_prompt(topic, articles, args.get("output_language", "zh")),
        },
        warnings,
    )


def tool_daily_brief(args: Dict[str, Any]) -> Dict[str, Any]:
    categories = as_list(args.get("categories")) or DEFAULT_DAILY_CATEGORIES
    country = args.get("country") or default_country()
    items_per_category = clamp_int(args.get("items_per_category"), 4, 1, 8)
    warnings: List[str] = []
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for raw_category in categories:
        category = normalize_category(raw_category) or raw_category
        articles, sub_warnings = collect_headlines(category, country, items_per_category, include_rss=True)
        sections[category] = articles[:items_per_category]
        warnings.extend(sub_warnings)
    return make_result(
        "daily_brief",
        {
            "country": country,
            "sections": sections,
            "copilot_prompt": daily_brief_prompt(sections, args.get("output_language", "zh")),
        },
        warnings,
    )


def tokenize_for_trends(text: str) -> List[str]:
    tokens = []
    for match in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", text):
        lowered = match.lower()
        if lowered not in EN_STOPWORDS:
            tokens.append(match)
    for match in re.findall(r"[\u4e00-\u9fff]{2,6}", text):
        if match not in {"新闻", "报道", "最新", "今日", "相关"}:
            tokens.append(match)
    return tokens


def tool_trending_topics(args: Dict[str, Any]) -> Dict[str, Any]:
    category = args.get("category")
    max_topics = clamp_int(args.get("max_topics"), 10, 1, 30)
    country = args.get("country") or default_country()
    articles, warnings = collect_headlines(category, country, 50, include_rss=True)
    counts: Dict[str, int] = {}
    examples: Dict[str, List[Dict[str, Any]]] = {}
    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')}"
        seen_for_article = set()
        for token in tokenize_for_trends(text):
            key = token.lower()
            if key in seen_for_article:
                continue
            seen_for_article.add(key)
            counts[key] = counts.get(key, 0) + 1
            examples.setdefault(key, []).append(article)
    topics = []
    for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:max_topics]:
        label = examples[key][0].get("title", key)
        topics.append(
            {
                "topic": key,
                "mentions": count,
                "sample_headline": label,
                "articles": examples[key][:3],
            }
        )
    return make_result(
        "trending_topics",
        {"category": normalize_category(category), "topics": topics},
        warnings,
    )


def tool_news_timeline(args: Dict[str, Any]) -> Dict[str, Any]:
    topic = str(args.get("topic") or "").strip()
    if not topic:
        raise McpError("news_timeline requires topic.", code=-32602)
    days_back = clamp_int(args.get("days_back"), 14, 1, 30)
    max_articles = clamp_int(args.get("max_articles"), 20, 1, 50)
    since = (utc_now() - dt.timedelta(days=days_back)).date().isoformat()
    articles, warnings = collect_search(
        query=topic,
        language=args.get("language"),
        from_date=since,
        to_date=None,
        sort_by="publishedAt",
        page_size=max_articles,
        include_rss=True,
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for article in sorted(articles, key=article_sort_key):
        parsed = article_sort_key(article)
        grouped.setdefault(parsed.date().isoformat(), []).append(article)
    prompt = (
        f"请基于以下新闻材料，为“{topic}”生成时间线。"
        "按日期列出关键变化、各方动作和不确定点，只使用材料中的信息。\n\n"
        + article_lines(articles, max_items=max_articles)
    )
    return make_result(
        "news_timeline",
        {"topic": topic, "timeline": grouped, "copilot_prompt": prompt},
        warnings,
    )


def load_preferences() -> Dict[str, Any]:
    return read_json(prefs_path(), {})


def tool_set_preferences(args: Dict[str, Any]) -> Dict[str, Any]:
    preferences = {
        "topics": as_list(args.get("topics")),
        "keywords": as_list(args.get("keywords")),
        "countries": as_list(args.get("countries")),
        "categories": [normalize_category(item) or item for item in as_list(args.get("categories"))],
        "languages": as_list(args.get("languages")),
        "updated_at": isoformat(utc_now()),
    }
    write_json(prefs_path(), preferences)
    return make_result(
        "set_preferences",
        {
            "preferences": preferences,
            "saved_to": display_path(prefs_path()),
            "note": "Preferences are local user data and are ignored by .gitignore.",
        },
    )


def tool_get_my_feed(args: Dict[str, Any]) -> Dict[str, Any]:
    prefs = load_preferences()
    if not prefs:
        return make_result(
            "get_my_feed",
            {
                "articles": [],
                "preferences": {},
                "message": "No preferences saved yet. Call set_preferences first.",
            },
        )
    page_size = clamp_int(args.get("page_size"), 20, 1, 50)
    country = (prefs.get("countries") or [default_country()])[0]
    warnings: List[str] = []
    pool: List[Dict[str, Any]] = []

    for category in prefs.get("categories") or []:
        articles, sub_warnings = collect_headlines(category, country, 8, include_rss=True)
        pool.extend(articles)
        warnings.extend(sub_warnings)

    topic_terms = list(dict.fromkeys((prefs.get("topics") or []) + (prefs.get("keywords") or [])))
    if topic_terms:
        query = " OR ".join(topic_terms[:6])
        language = (prefs.get("languages") or [default_language()])[0]
        articles, sub_warnings = collect_search(
            query=query,
            language=language,
            from_date=(utc_now() - dt.timedelta(days=7)).date().isoformat(),
            to_date=None,
            sort_by="publishedAt",
            page_size=page_size,
            include_rss=True,
        )
        pool.extend(articles)
        warnings.extend(sub_warnings)

    ranked = sorted_articles(pool)[:page_size]
    return make_result(
        "get_my_feed",
        {
            "preferences": prefs,
            "articles": ranked,
            "copilot_prompt": summary_prompt("my personalized news feed", ranked),
        },
        warnings,
    )


def tool_translation_prompt(args: Dict[str, Any], target_language: str) -> Dict[str, Any]:
    text = str(args.get("text") or "").strip()
    source_language = "Chinese" if target_language == "English" else "English"
    prompt = (
        f"Translate the following {source_language} news text into {target_language}. "
        "Keep names, dates, numbers, and source attributions accurate. "
        "If the text is a headline, keep the translation concise.\n\n"
        f"{text}"
    )
    return make_result(
        "zh_to_en" if target_language == "English" else "en_to_zh",
        {
            "source_text": text,
            "target_language": target_language,
            "copilot_prompt": prompt,
            "note": "This MCP server does not call an AI translation API; let Copilot perform the translation from this prompt.",
        },
    )


def tool_list_rss_sources(args: Dict[str, Any]) -> Dict[str, Any]:
    config = load_rss_config()
    return make_result(
        "list_rss_sources",
        {
            "config_path": display_path(rss_config_path()),
            "sources": config.get("sources", []),
            "how_to_add": "Edit config/rss_sources.json, add an object with name/url/category/language/region/enabled, then restart the MCP server.",
        },
    )


def tool_send_email(args: Dict[str, Any]) -> Dict[str, Any]:
    subject = str(args.get("subject") or "").strip()
    body = str(args.get("body") or args.get("content") or "").strip()
    body_format = str(args.get("body_format") or "plain").strip().lower()

    if not subject:
        raise McpError("send_email requires subject.", code=-32602)
    if not body:
        raise McpError("send_email requires body.", code=-32602)

    recipients = as_list(args.get("to")) or as_list(os.environ.get("EMAIL_TO") or os.environ.get("MAIL_TO"))
    if not recipients:
        raise McpError("No email recipient configured. Set EMAIL_TO in .env or pass the to argument.", code=-32602)

    host = os.environ.get("SMTP_HOST", "").strip()
    port = clamp_int(os.environ.get("SMTP_PORT"), 587, 1, 65535)
    username = os.environ.get("SMTP_USERNAME", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    from_addr = os.environ.get("EMAIL_FROM", "").strip() or username
    use_tls = env_bool("SMTP_USE_TLS", True)
    use_ssl = env_bool("SMTP_USE_SSL", False)

    missing = []
    if not host:
        missing.append("SMTP_HOST")
    if not from_addr:
        missing.append("EMAIL_FROM or SMTP_USERNAME")
    if username and not password:
        missing.append("SMTP_PASSWORD")
    if missing:
        raise McpError(f"Missing email configuration: {', '.join(missing)}", code=-32602)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_addr
    message["To"] = ", ".join(recipients)
    message["Date"] = email.utils.formatdate(localtime=True)
    if body_format == "html":
        message.set_content(clean_text(body) or "This email contains an HTML version of the news brief.")
    else:
        message.set_content(body)
    html_body, template_used = render_email_template(subject, body, body_format)
    message.add_alternative(html_body, subtype="html")

    try:
        if use_ssl:
            smtp = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
        with smtp:
            smtp.ehlo()
            if use_tls and not use_ssl:
                smtp.starttls()
                smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    except Exception as exc:
        raise McpError(f"Failed to send email: {type(exc).__name__}: {exc}", code=-32000)

    return make_result(
        "send_email",
        {
            "sent": True,
            "subject": subject,
            "recipient_count": len(recipients),
            "recipients": [mask_email(item) for item in recipients],
            "smtp_host": host,
            "template": template_used,
            "note": "Email sent. SMTP password was not returned or logged.",
        },
    )


TOOL_HANDLERS = {
    "get_headlines": tool_get_headlines,
    "search_news": tool_search_news,
    "get_news_by_category": tool_get_news_by_category,
    "get_top_stories": tool_get_top_stories,
    "summarize_news": tool_summarize_news,
    "daily_brief": tool_daily_brief,
    "trending_topics": tool_trending_topics,
    "news_timeline": tool_news_timeline,
    "set_preferences": tool_set_preferences,
    "get_my_feed": tool_get_my_feed,
    "zh_to_en": lambda args: tool_translation_prompt(args, "English"),
    "en_to_zh": lambda args: tool_translation_prompt(args, "Chinese"),
    "list_rss_sources": tool_list_rss_sources,
    "send_email": tool_send_email,
}


def schema_for_tools() -> List[Dict[str, Any]]:
    category_desc = "Category alias: general, world/international, domestic, technology, business, sports, science, health, entertainment; Chinese aliases like 科技/财经/国内/国际 also work."
    return [
        {
            "name": "get_headlines",
            "description": "Get current headlines from NewsAPI and configured RSS feeds.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": category_desc},
                    "country": {"type": "string", "description": "NewsAPI country code, e.g. us, gb, cn."},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "include_rss": {"type": "boolean", "default": True},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional NewsAPI source ids. When set, country/category are ignored by NewsAPI.",
                    },
                },
            },
        },
        {
            "name": "search_news",
            "description": "Search recent news by keyword or topic.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "language": {"type": "string", "description": "NewsAPI language code, e.g. en, zh."},
                    "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "to_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "sort_by": {
                        "type": "string",
                        "enum": ["publishedAt", "relevancy", "popularity"],
                        "default": "publishedAt",
                    },
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "include_rss": {"type": "boolean", "default": True},
                },
            },
        },
        {
            "name": "get_news_by_category",
            "description": "Browse news by category. This is a convenience wrapper around get_headlines.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": category_desc},
                    "country": {"type": "string"},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "include_rss": {"type": "boolean", "default": True},
                },
            },
        },
        {
            "name": "get_top_stories",
            "description": "Collect and rank a few important stories across categories, then provide a Copilot summary prompt.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "country": {"type": "string"},
                    "total": {"type": "integer", "minimum": 1, "maximum": 30, "default": 8},
                },
            },
        },
        {
            "name": "summarize_news",
            "description": "Fetch articles for a topic and return a prompt for Copilot to summarize them.",
            "inputSchema": {
                "type": "object",
                "required": ["topic"],
                "properties": {
                    "topic": {"type": "string"},
                    "language": {"type": "string"},
                    "output_language": {"type": "string", "default": "zh"},
                    "days_back": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7},
                    "max_articles": {"type": "integer", "minimum": 1, "maximum": 30, "default": 12},
                },
            },
        },
        {
            "name": "daily_brief",
            "description": "Build a structured daily brief dataset and a Copilot prompt.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "country": {"type": "string"},
                    "items_per_category": {"type": "integer", "minimum": 1, "maximum": 8, "default": 4},
                    "output_language": {"type": "string", "default": "zh"},
                },
            },
        },
        {
            "name": "trending_topics",
            "description": "Estimate trending terms from current headlines. Lightweight keyword counting, not a professional trend model.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "country": {"type": "string"},
                    "max_topics": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
            },
        },
        {
            "name": "news_timeline",
            "description": "Fetch recent articles for a topic, group them by date, and provide a Copilot timeline prompt.",
            "inputSchema": {
                "type": "object",
                "required": ["topic"],
                "properties": {
                    "topic": {"type": "string"},
                    "language": {"type": "string"},
                    "days_back": {"type": "integer", "minimum": 1, "maximum": 30, "default": 14},
                    "max_articles": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                },
            },
        },
        {
            "name": "set_preferences",
            "description": "Save local personalization preferences for get_my_feed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topics": {"type": "array", "items": {"type": "string"}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "countries": {"type": "array", "items": {"type": "string"}},
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "languages": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        {
            "name": "get_my_feed",
            "description": "Get a personalized feed from saved preferences.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}
                },
            },
        },
        {
            "name": "zh_to_en",
            "description": "Return a Copilot prompt to translate Chinese news text into English.",
            "inputSchema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        },
        {
            "name": "en_to_zh",
            "description": "Return a Copilot prompt to translate English news text into Chinese.",
            "inputSchema": {
                "type": "object",
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
        },
        {
            "name": "list_rss_sources",
            "description": "List configured RSS feeds and show where to add more.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "send_email",
            "description": "Send a news brief or any agent-generated note to the configured private mailbox via SMTP.",
            "inputSchema": {
                "type": "object",
                "required": ["subject", "body"],
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string", "description": "Plain text or HTML email body."},
                    "body_format": {
                        "type": "string",
                        "enum": ["plain", "html"],
                        "default": "plain",
                    },
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional recipients. Prefer EMAIL_TO in .env for privacy.",
                    },
                },
            },
        },
    ]


def json_text_response(data: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, ensure_ascii=False, indent=2),
            }
        ],
        "isError": is_error,
    }


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": schema_for_tools()}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOL_HANDLERS:
            raise McpError(f"Unknown tool: {name}", code=-32602)
        result = TOOL_HANDLERS[name](arguments)
        return {"jsonrpc": "2.0", "id": request_id, "result": json_text_response(result)}
    if method in {"resources/list", "prompts/list"}:
        key = "resources" if method == "resources/list" else "prompts"
        return {"jsonrpc": "2.0", "id": request_id, "result": {key: []}}
    raise McpError(f"Method not found: {method}", code=-32601)


def error_response(request_id: Any, exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, McpError):
        error: Dict[str, Any] = {"code": exc.code, "message": str(exc)}
        if exc.data is not None:
            error["data"] = exc.data
    else:
        error = {"code": -32000, "message": str(exc)}
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def serve_stdio() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            response = handle_request(request)
            if response is None:
                continue
        except Exception as exc:
            if not isinstance(exc, McpError):
                log(traceback.format_exc())
            response = error_response(request_id, exc)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
