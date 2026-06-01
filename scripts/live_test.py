#!/usr/bin/env python3
"""Live test for NewsAPI + RSS.

This consumes a small number of NewsAPI requests and never prints the API key.
"""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import news_mcp_server as server  # noqa: E402


def compact_articles(articles, limit=5):
    result = []
    for article in articles[:limit]:
        result.append(
            {
                "title": article.get("title"),
                "source": article.get("source"),
                "published_at": article.get("published_at"),
                "origin": article.get("origin"),
                "url": article.get("url"),
            }
        )
    return result


def main():
    checks = {}

    headlines = server.tool_get_headlines(
        {
            "category": "technology",
            "country": "us",
            "page_size": 3,
            "include_rss": False,
        }
    )
    checks["newsapi_headlines"] = {
        "count": len(headlines.get("articles", [])),
        "articles": compact_articles(headlines.get("articles", []), 3),
        "warnings": headlines.get("warnings", []),
    }

    search = server.tool_search_news(
        {
            "query": "artificial intelligence",
            "language": "en",
            "page_size": 3,
            "include_rss": False,
        }
    )
    checks["newsapi_search"] = {
        "count": len(search.get("articles", [])),
        "articles": compact_articles(search.get("articles", []), 3),
        "warnings": search.get("warnings", []),
    }

    rss_articles, rss_warnings = server.fetch_rss(category="world", max_articles=5)
    checks["rss_world"] = {
        "count": len(rss_articles),
        "articles": compact_articles(rss_articles, 5),
        "warnings": rss_warnings[:5],
    }

    print(json.dumps(checks, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
