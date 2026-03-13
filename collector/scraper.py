# collector/scraper.py
"""Scrape HTML pages for sources that don't offer RSS feeds."""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from typing import Optional
from collector.rss_collector import RawItem
import re


HEADERS = {
    "User-Agent": "PolicyTrendAgent/1.0 (research bot; policy monitoring)"
}


def parse_date_string(date_str: str) -> Optional[datetime]:
    """Try to parse a date string in various common formats."""
    if not date_str:
        return None

    date_str = re.sub(r"\s+", " ", date_str.strip())
    formats = [
        "%d %B %Y", "%B %d, %Y", "%d/%m/%Y", "%Y-%m-%d",
        "%d %b %Y", "%b %d, %Y", "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def scrape_source(source: dict, lookback_hours: int = 24) -> list[RawItem]:
    """
    Scrape a single source using CSS selectors defined in sources.yaml.
    Falls back to extracting all <a> tags with article-like context.
    """
    items = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    try:
        resp = requests.get(source["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Scrape failed for {source['id']}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # Find article containers
    containers = []
    selector = source.get("selector")
    if selector:
        containers = soup.select(selector)

    # Fallback: try common article patterns
    if not containers:
        for tag in ["article", "div.news-item", "li.news-item", "div.listing-item"]:
            containers = soup.select(tag)
            if containers:
                break

    # Last resort: just grab all links from the page
    if not containers:
        containers = [soup]

    link_selector = source.get("link_selector", "a")
    date_selector = source.get("date_selector", "time, span.date, .date, time[datetime]")
    keywords_filter = source.get("keywords_filter", [])

    for container in containers[:50]:  # cap per page
        # Find link
        link_el = container.select_one(link_selector)
        if not link_el:
            continue

        title = link_el.get_text(strip=True)
        href = link_el.get("href", "")

        # Make absolute URL
        if href.startswith("/"):
            from urllib.parse import urlparse
            base = urlparse(source["url"])
            href = f"{base.scheme}://{base.netloc}{href}"
        elif not href.startswith("http"):
            continue

        # Find date
        date_el = container.select_one(date_selector) if date_selector else None
        published = None
        if date_el:
            date_text = date_el.get("datetime") or date_el.get_text(strip=True)
            published = parse_date_string(date_text)

        # Skip if too old (only if we have a date)
        if published and published < cutoff:
            continue

        # Summary = paragraph text in container
        summary_el = container.select_one("p")
        summary = summary_el.get_text(strip=True)[:500] if summary_el else ""

        # Keyword filter
        if keywords_filter:
            combined = (title + " " + summary).lower()
            if not any(kw.lower() in combined for kw in keywords_filter):
                continue

        if not title or not href:
            continue

        items.append(RawItem(
            source_id=source["id"],
            title=title,
            url=href,
            published=published,
            summary=summary,
            jurisdiction=source["jurisdiction"],
            domains=source.get("domain", []),
        ))

    return items


def fetch_all_scraped(sources: list[dict], lookback_hours: int = 24) -> list[RawItem]:
    """Scrape all scrape-type sources."""
    all_items = []
    scrape_sources = [s for s in sources if s.get("type") == "scrape"]

    for source in scrape_sources:
        print(f"  Scraping: {source['name']} ({source['id']})")
        items = scrape_source(source, lookback_hours)
        print(f"    → {len(items)} items")
        all_items.extend(items)

    return all_items
