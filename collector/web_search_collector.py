# collector/web_search_collector.py
"""
Tier 1 — Broad web search sweep using Claude's web_search tool.
Runs a set of targeted queries across all domains and jurisdictions
to catch breaking developments that RSS feeds may miss.
Returns RawItem objects just like the other collectors.
"""

import anthropic
import json
from datetime import datetime, timezone
from collector.rss_collector import RawItem


# Search queries: broad landscape first, then jurisdiction-specific
SEARCH_QUERIES = [
    # Cross-jurisdictional landscape
    {"q": "AI safety regulation policy 2025 2026", "jurisdiction": "global", "domain": ["ai_safety", "tech_governance"]},
    {"q": "online safety law enforcement action 2025 2026", "jurisdiction": "global", "domain": ["online_safety"]},
    {"q": "AI governance regulation news this week", "jurisdiction": "global", "domain": ["ai_safety", "tech_governance"]},
    {"q": "tech regulation policy developments this month", "jurisdiction": "global", "domain": ["tech_governance"]},

    # Singapore
    {"q": "Singapore AI policy regulation 2025 2026", "jurisdiction": "sg", "domain": ["ai_safety", "tech_governance"]},
    {"q": "Singapore online safety digital regulation news", "jurisdiction": "sg", "domain": ["online_safety", "tech_governance"]},
    {"q": "IMDA PDPC MAS digital regulation announcement", "jurisdiction": "sg", "domain": ["tech_governance", "ai_safety"]},

    # EU
    {"q": "EU AI Act implementation enforcement 2025 2026", "jurisdiction": "eu", "domain": ["ai_safety", "tech_governance"]},
    {"q": "Digital Services Act DSA enforcement action Europe", "jurisdiction": "eu", "domain": ["online_safety", "tech_governance"]},
    {"q": "GDPR enforcement fine ruling 2025 2026", "jurisdiction": "eu", "domain": ["tech_governance"]},
    {"q": "European Commission digital policy AI regulation news", "jurisdiction": "eu", "domain": ["ai_safety", "tech_governance"]},

    # Australia
    {"q": "Australia AI regulation online safety policy 2025 2026", "jurisdiction": "au", "domain": ["ai_safety", "online_safety"]},
    {"q": "eSafety Commissioner ACMA enforcement Australia", "jurisdiction": "au", "domain": ["online_safety"]},

    # UK
    {"q": "UK AI Safety Institute regulation policy 2025 2026", "jurisdiction": "uk", "domain": ["ai_safety"]},
    {"q": "Ofcom Online Safety Act enforcement UK", "jurisdiction": "uk", "domain": ["online_safety"]},

    # ASEAN
    {"q": "ASEAN AI governance digital regulation 2025 2026", "jurisdiction": "asean", "domain": ["ai_safety", "tech_governance"]},
    {"q": "Southeast Asia online safety tech regulation news", "jurisdiction": "asean", "domain": ["online_safety", "tech_governance"]},
]

EXTRACT_PROMPT = """You searched the web for: "{query}"

Here are the search results:
{results}

Extract up to 5 distinct, relevant policy/regulatory items from these results.
Focus on: new laws, consultations, enforcement actions, guidance, significant reports.
Ignore opinion pieces, duplicates, and items not related to online safety, AI safety, or tech governance.

Return a JSON array (no markdown, no explanation):
[
  {{
    "title": "...",
    "url": "...",
    "summary": "1-2 sentence description of what happened and why it matters",
    "jurisdiction": "{jurisdiction}",
    "domain": "online_safety | ai_safety | tech_governance | other"
  }}
]

If no relevant items found, return an empty array: []"""


class WebSearchCollector:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def _run_search(self, query: str, jurisdiction: str) -> list[dict]:
        """Run a single web search query via Claude's web_search tool."""
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": f"Search the web for recent news about: {query}\n\nFocus on results from the last 30 days. Return the search results."
                }]
            )

            # Extract search results from tool use blocks
            search_results = []
            for block in response.content:
                if hasattr(block, "type") and block.type == "tool_result":
                    search_results.append(str(block))
                elif hasattr(block, "text"):
                    search_results.append(block.text)

            return "\n\n".join(search_results)

        except Exception as e:
            print(f"    [WARN] Web search failed for '{query}': {e}")
            return ""

    def _extract_items(self, query: str, results: str, jurisdiction: str) -> list[dict]:
        """Ask Claude to extract structured items from search results."""
        if not results:
            return []

        try:
            prompt = EXTRACT_PROMPT.format(
                query=query,
                results=results[:4000],
                jurisdiction=jurisdiction,
            )
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(raw)
        except Exception as e:
            print(f"    [WARN] Item extraction failed: {e}")
            return []

    def collect(self, max_queries: int = 10) -> list[RawItem]:
        """
        Run web search queries and return RawItems.
        max_queries caps API usage — increase for broader coverage.
        """
        all_items = []
        seen_urls = set()
        queries = SEARCH_QUERIES[:max_queries]

        print(f"\n[Web Search] Running {len(queries)} search queries...")

        for q_config in queries:
            query = q_config["q"]
            jurisdiction = q_config["jurisdiction"]
            domains = q_config["domain"]

            print(f"  Searching: {query[:60]}...")

            results = self._run_search(query, jurisdiction)
            if not results:
                continue

            extracted = self._extract_items(query, results, jurisdiction)
            new = 0
            for item in extracted:
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                all_items.append(RawItem(
                    source_id="web_search",
                    title=item.get("title", "").strip(),
                    url=url,
                    published=datetime.now(timezone.utc),
                    summary=item.get("summary", "").strip()[:800],
                    jurisdiction=item.get("jurisdiction", jurisdiction),
                    domains=domains,
                ))
                new += 1

            print(f"    → {new} new items extracted")

        print(f"[Web Search] Total: {len(all_items)} items from web search\n")
        return all_items
