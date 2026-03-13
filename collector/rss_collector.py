# collector/rss_collector.py
"""Fetch and normalise items from RSS/Atom feeds."""

import feedparser
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class RawItem:
    source_id: str
    title: str
    url: str
    published: Optional[datetime]
    summary: str
    jurisdiction: str
    domains: list[str]
    raw_text: str = ""


def fetch_rss(source: dict, lookback_hours: int = 24) -> list[RawItem]:
    """
    Fetch items from a single RSS source config dict.
    Returns items published within lookback_hours.
    """
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    try:
        headers = {"User-Agent": "PolicyTrendAgent/1.0 (research bot)"}
        resp = requests.get(source["url"], headers=headers, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        print(f"  [WARN] RSS fetch failed for {source['id']}: {e}")
        return []

    keywords_filter = source.get("keywords_filter", [])

    for entry in feed.entries:
        # Parse date
        published = None
        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            if hasattr(entry, attr) and getattr(entry, attr):
                t = getattr(entry, attr)
                try:
                    published = datetime(*t[:6], tzinfo=timezone.utc)
                    break
                except Exception:
                    pass

        # Skip if too old
        if published and published < cutoff:
            continue

        title = getattr(entry, "title", "").strip()
        url = getattr(entry, "link", "").strip()
        summary = getattr(entry, "summary", "").strip()

        # Apply keyword filter if specified
        if keywords_filter:
            combined = (title + " " + summary).lower()
            if not any(kw.lower() in combined for kw in keywords_filter):
                continue

        if not title or not url:
            continue

        items.append(RawItem(
            source_id=source["id"],
            title=title,
            url=url,
            published=published,
            summary=summary[:1000],
            jurisdiction=source["jurisdiction"],
            domains=source.get("domain", []),
        ))

    return items


def fetch_all_rss(sources: list[dict], lookback_hours: int = 24) -> list[RawItem]:
    """Fetch all RSS sources and return combined list."""
    all_items = []
    rss_sources = [s for s in sources if s.get("type") == "rss"]

    for source in rss_sources:
        print(f"  Fetching RSS: {source['name']} ({source['id']})")
        items = fetch_rss(source, lookback_hours)
        print(f"    → {len(items)} items")
        all_items.extend(items)

    return all_items
