# collector/web_search_collector.py
"""
Tier 1 — Broad web search with trusted source verification.
Cost optimised: batch verification (all items per query in ONE call).

Flow:
  1. Search finds items from any source
  2. Items already from trusted domains pass through immediately
  3. Remaining items verified in one batch call per query
  4. Verified items get upgraded URL; unverified are flagged
"""

import anthropic
import json
import os
from datetime import datetime, timezone
from collector.rss_collector import RawItem

HAIKU_MODEL = "claude-haiku-4-5-20251001"

TRUSTED_DOMAINS = {
    # International news wires & outlets
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com",
    "wsj.com", "nytimes.com", "theguardian.com", "bbc.com", "bbc.co.uk",
    "axios.com", "politico.com", "politico.eu", "economist.com",
    "washingtonpost.com", "theatlantic.com",
    "wired.com", "technologyreview.com", "techcrunch.com",
    "arstechnica.com", "theregister.com",

    # SG news
    "straitstimes.com", "channelnewsasia.com", "todayonline.com",
    "businesstimes.com.sg",

    # Regional / international
    "scmp.com", "nikkei.com", "abc.net.au",

    # EU / UK news
    "euractiv.com",

    # Official government / regulatory
    "gov.sg", "imda.gov.sg", "pdpc.gov.sg", "mas.gov.sg",
    "csa.gov.sg", "tech.gov.sg", "smartnation.gov.sg",
    "gov.uk", "ofcom.org.uk", "ico.org.uk",
    "gov.au", "acma.gov.au", "oaic.gov.au", "esafety.gov.au",
    "europa.eu", "ec.europa.eu", "edpb.europa.eu", "enisa.europa.eu",
    "asean.org", "oecd.org", "oecd.ai",

    # Think tanks & academic
    "brookings.edu", "rand.org", "chathamhouse.org", "csis.org",
    "adalovelaceinstitute.org", "ainowinstitute.org", "futureoflife.org",
    "techpolicy.press", "algorithmwatch.org", "accessnow.org",
    "ceps.eu", "edri.org", "lkyspp.nus.edu.sg",

    # Legal / policy
    "lexology.com", "iapp.org", "linklaters.com", "dlapiper.com",
}

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

COMBINED_PROMPT = """Search the web for recent news (last 14 days) about: {query}

Extract up to 4 relevant policy/regulatory items.
Only include: new laws, consultations, enforcement actions, guidance, significant reports.
Ignore: opinion pieces, duplicates, unrelated news.

Return ONLY a JSON array, no markdown:
[
  {{
    "title": "exact headline",
    "url": "https://...",
    "summary": "1 sentence: what happened and why it matters",
    "jurisdiction": "sg|au|uk|eu|asean|global",
    "domain": "online_safety|ai_safety|tech_governance|other"
  }}
]

If nothing relevant found, return: []"""

BATCH_VERIFY_PROMPT = """Search the web to find trusted source coverage for each story below.
Trusted sources: Reuters, AP, Bloomberg, FT, BBC, Guardian, NYT, Washington Post,
The Atlantic, Axios, Politico, CNA, Straits Times, Business Times, ABC Australia,
official .gov sites, europa.eu, OECD, or established think tanks.

Stories to verify:
{stories}

Return a JSON array with one result per story in the same order:
[
  {{
    "found": true,
    "trusted_url": "https://...",
    "trusted_source": "Reuters",
    "title": "headline from trusted source"
  }},
  {{
    "found": false
  }}
]

Return only the JSON array, no explanation."""


def _get_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_trusted(url: str) -> bool:
    domain = _get_domain(url)
    return any(domain == td or domain.endswith("." + td) for td in TRUSTED_DOMAINS)


class WebSearchCollector:
    def __init__(self, api_key: str, model: str = HAIKU_MODEL, usage=None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model  = model
        self.usage  = usage

    def _should_run(self) -> bool:
        if os.environ.get("WEB_SEARCH_EVERY_RUN", "").lower() == "true":
            return True
        flag_path = "/tmp/web_search_last_run"
        try:
            if os.path.exists(flag_path):
                with open(flag_path) as f:
                    last = int(f.read().strip())
                if int(datetime.now().timestamp()) - last < 3600 * 10:
                    print("[Web Search] Skipping this cycle (ran recently).")
                    return False
        except Exception:
            pass
        with open(flag_path, "w") as f:
            f.write(str(int(datetime.now().timestamp())))
        return True

    def _search_and_extract(self, query: str) -> list[dict]:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=800,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": COMBINED_PROMPT.format(query=query)}]
            )
            if self.usage:
                self.usage.add(response, model=self.model)
            for block in reversed(response.content):
                if hasattr(block, "text"):
                    raw = block.text.strip().replace("```json", "").replace("```", "").strip()
                    if raw.startswith("["):
                        return json.loads(raw)
            return []
        except Exception as e:
            print(f"    [WARN] Search failed: {e}")
            return []

    def _verify_batch(self, items: list[dict]) -> list[dict]:
        """Verify all untrusted items in ONE API call."""
        trusted, unverified = [], []
        for item in items:
            if _is_trusted(item.get("url", "")):
                item["verified"]       = True
                item["trusted_source"] = _get_domain(item["url"])
                trusted.append(item)
            else:
                unverified.append(item)

        if not unverified:
            return trusted

        stories = "\n".join(
            f"{i+1}. {item.get('title', '')}"
            for i, item in enumerate(unverified)
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=600,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": BATCH_VERIFY_PROMPT.format(stories=stories)}]
            )
            if self.usage:
                self.usage.add(response, model=self.model)

            for block in reversed(response.content):
                if hasattr(block, "text"):
                    raw = block.text.strip().replace("```json", "").replace("```", "").strip()
                    if raw.startswith("["):
                        results = json.loads(raw)
                        for item, result in zip(unverified, results):
                            if result.get("found"):
                                item["url"]            = result.get("trusted_url", item["url"])
                                item["verified"]       = True
                                item["trusted_source"] = result.get("trusted_source", "")
                                item["title"]          = result.get("title", item["title"])
                                print(f"    ✓ Verified [{item['trusted_source']}]: {item['title'][:50]}")
                            else:
                                item["verified"]       = False
                                item["trusted_source"] = _get_domain(item.get("url", ""))
                                print(f"    ~ Unverified: {item['title'][:50]}")
                        break
        except Exception as e:
            print(f"    [WARN] Batch verification failed: {e}")
            for item in unverified:
                item["verified"]       = False
                item["trusted_source"] = _get_domain(item.get("url", ""))

        return trusted + unverified

    def collect(self, max_queries: int = 5) -> list[RawItem]:
        if not self._should_run():
            return []

        all_items  = []
        seen_urls  = set()
        queries    = SEARCH_QUERIES[:max_queries]
        verified   = 0
        unverified = 0

        print(f"\n[Web Search] Running {len(queries)} queries with trusted source verification...")

        for q_config in queries:
            print(f"  → {q_config['q'][:65]}...")
            extracted = self._search_and_extract(q_config["q"])

            # Dedupe before verification
            fresh = []
            for item in extracted:
                url = item.get("url", "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    fresh.append(item)

            if not fresh:
                print(f"     0 new items")
                continue

            print(f"    Verifying {len(fresh)} items...")
            verified_batch = self._verify_batch(fresh)

            for item in verified_batch:
                final_url = item.get("url", "").strip()
                if not final_url or not item.get("title", "").strip():
                    continue
                seen_urls.add(final_url)

                source_id = "web_search_verified" if item.get("verified") else "web_search_unverified"
                if item.get("verified"):
                    verified += 1
                else:
                    unverified += 1

                trust_note = f"[{item['trusted_source']}] " if item.get("trusted_source") else ""
                summary = f"{trust_note}{item.get('summary', '')}".strip()

                all_items.append(RawItem(
                    source_id=source_id,
                    title=item["title"].strip(),
                    url=final_url,
                    published=datetime.now(timezone.utc),
                    summary=summary[:600],
                    jurisdiction=item.get("jurisdiction", q_config["jurisdiction"]),
                    domains=q_config["domain"],
                ))

        print(f"[Web Search] Total: {len(all_items)} items "
              f"(✓ verified: {verified}, ~ unverified: {unverified})\n")
        return all_items
