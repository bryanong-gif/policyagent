# collector/web_search_collector.py
"""
Tier 1 — Broad web search sweep using Claude's web_search tool.
Cost-optimised: search + extract in ONE call per query (not two).
Truncates results aggressively to minimise input tokens.
Runs every other cycle (every 12h) to halve frequency cost.
"""

import anthropic
import json
import os
from datetime import datetime, timezone
from collector.rss_collector import RawItem

HAIKU_MODEL = "claude-haiku-4-5-20251001"


# Consolidated queries — broader, fewer, more token-efficient
# Each query covers multiple jurisdictions/topics to reduce total call count
SEARCH_QUERIES = [
    {
        "q": "Singapore AI online safety tech governance regulation news 2026",
        "jurisdiction": "sg",
        "domain": ["ai_safety", "online_safety", "tech_governance"]
    },
    {
        "q": "UK Australia AI safety online safety regulation enforcement 2026",
        "jurisdiction": "global",
        "domain": ["ai_safety", "online_safety", "tech_governance"]
    },
    {
        "q": "ASEAN Southeast Asia digital tech governance AI policy 2026",
        "jurisdiction": "asean",
        "domain": ["ai_safety", "tech_governance", "online_safety"]
    },
    {
        "q": "artificial intelligence regulation policy law enforcement global 2026",
        "jurisdiction": "global",
        "domain": ["ai_safety", "tech_governance"]
    },
    {
        "q": "online safety social media content moderation law 2026",
        "jurisdiction": "global",
        "domain": ["online_safety", "tech_governance"]
    },
    {
        "q": "EU AI Act DSA GDPR enforcement digital regulation 2026",
        "jurisdiction": "eu",
        "domain": ["ai_safety", "online_safety", "tech_governance"]
    },
]

# Single combined prompt — search AND extract in one call
COMBINED_PROMPT = """Search the web for recent news (last 14 days) about: {query}

After searching, extract up to 4 relevant policy/regulatory items from the results.
Only include: new laws, consultations, enforcement actions, guidance, significant reports.
Ignore: opinion pieces, duplicates, unrelated news.

Return ONLY a JSON array, no markdown, no explanation:
[
  {{
    "title": "exact headline",
    "url": "https://...",
    "summary": "1 sentence: what happened and why it matters for online safety/AI/tech governance",
    "jurisdiction": "sg|au|uk|eu|asean|global",
    "domain": "online_safety|ai_safety|tech_governance|other"
  }}
]

If nothing relevant found, return: []"""


class WebSearchCollector:
    def __init__(self, api_key: str, model: str = HAIKU_MODEL, usage=None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model  = model
        self.usage  = usage

    def _should_run(self) -> bool:
        """
        Run web search every other agent cycle (~every 12h) to halve cost.
        Uses a simple file-based flag. On Railway, resets each deploy.
        Set WEB_SEARCH_EVERY_RUN=true to always run.
        """
        if os.environ.get("WEB_SEARCH_EVERY_RUN", "").lower() == "true":
            return True

        flag_path = "/tmp/web_search_last_run"
        try:
            if os.path.exists(flag_path):
                with open(flag_path) as f:
                    last = int(f.read().strip())
                now = int(datetime.now().timestamp())
                if now - last < 3600 * 10:   # less than 10h ago → skip
                    print("[Web Search] Skipping this cycle (ran recently). Set WEB_SEARCH_EVERY_RUN=true to override.")
                    return False
        except Exception:
            pass

        with open(flag_path, "w") as f:
            f.write(str(int(datetime.now().timestamp())))
        return True

    def _search_and_extract(self, q_config: dict) -> list[dict]:
        """Single API call: search the web and extract items in one shot."""
        query      = q_config["q"]
        prompt     = COMBINED_PROMPT.format(query=query)

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=800,          # tight cap — JSON output only
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": prompt}]
            )
            if self.usage:
                self.usage.add(response, model=self.model)

            # Find the text block containing our JSON
            for block in reversed(response.content):
                if hasattr(block, "text") and block.text.strip().startswith("["):
                    raw = block.text.strip()
                    raw = raw.replace("```json", "").replace("```", "").strip()
                    return json.loads(raw)

            # Fallback: try last text block anyway
            for block in reversed(response.content):
                if hasattr(block, "text"):
                    raw = block.text.strip()
                    raw = raw.replace("```json", "").replace("```", "").strip()
                    if raw.startswith("["):
                        return json.loads(raw)

            return []

        except json.JSONDecodeError:
            print(f"    [WARN] JSON parse error for query: {query[:50]}")
            return []
        except Exception as e:
            print(f"    [WARN] Web search failed for '{query[:50]}': {e}")
            return []

    def collect(self, max_queries: int = 6) -> list[RawItem]:
        """Run web search queries. Returns RawItems merged with RSS pool."""
        if not self._should_run():
            return []

        all_items = []
        seen_urls = set()
        queries   = SEARCH_QUERIES[:max_queries]

        print(f"\n[Web Search] Running {len(queries)} combined search+extract queries...")

        for q_config in queries:
            print(f"  → {q_config['q'][:65]}...")
            extracted = self._search_and_extract(q_config)
            new = 0

            for item in extracted:
                url = item.get("url", "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = item.get("title", "").strip()
                if not title:
                    continue

                all_items.append(RawItem(
                    source_id="web_search",
                    title=title,
                    url=url,
                    published=datetime.now(timezone.utc),
                    summary=item.get("summary", "").strip()[:600],
                    jurisdiction=item.get("jurisdiction", q_config["jurisdiction"]),
                    domains=q_config["domain"],
                ))
                new += 1

            print(f"     {new} items extracted")

        print(f"[Web Search] Total: {len(all_items)} unique items\n")
        return all_items
