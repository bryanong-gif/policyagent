# analyser/claude_analyser.py
"""
Claude API analysis layer.
Classifies, summarises, and scores each collected item.
Tracks token usage and estimated cost across all calls.
"""

import anthropic
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from collector.rss_collector import RawItem

# Pricing for claude-sonnet-4 (per million tokens)
INPUT_COST_PER_M  = 3.00
OUTPUT_COST_PER_M = 15.00


def _cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000 * INPUT_COST_PER_M +
            output_tokens / 1_000_000 * OUTPUT_COST_PER_M)


@dataclass
class TokenUsage:
    """Accumulated token usage across an entire agent run."""
    input_tokens:  int = 0
    output_tokens: int = 0
    api_calls:     int = 0

    def add(self, response):
        """Add usage from an Anthropic response object."""
        if hasattr(response, "usage") and response.usage:
            self.input_tokens  += getattr(response.usage, "input_tokens",  0)
            self.output_tokens += getattr(response.usage, "output_tokens", 0)
        self.api_calls += 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        return _cost(self.input_tokens, self.output_tokens)

    def report(self) -> str:
        return (
            f"API calls: {self.api_calls} | "
            f"Tokens: {self.total_tokens:,} "
            f"(in: {self.input_tokens:,} / out: {self.output_tokens:,}) | "
            f"Est. cost: ${self.estimated_cost_usd:.4f}"
        )


SYSTEM_PROMPT = """You are a senior policy analyst specialising in:
- Online safety regulation
- AI safety and governance
- Technology policy and data regulation

Your role is to analyse policy-related content from five jurisdictions:
Singapore (sg), Australia (au), United Kingdom (uk), European Union (eu), ASEAN (asean), and global.

Be precise, neutral, and concise. Avoid speculation. Flag when something is genuinely significant."""


ITEM_ANALYSIS_PROMPT = """Analyse this policy/regulatory item and return a JSON object with exactly these fields:

{{
  "summary": "<2-3 sentence plain-English summary of what this item is about and why it matters>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "domain": "<primary domain: online_safety | ai_safety | tech_governance | other>",
  "content_type": "<legislation | consultation | enforcement | enforcement_action | guidance | academic | news | speech | other>",
  "urgency": "<monitoring | notable | urgent>",
  "sentiment": "<regulatory_tightening | regulatory_loosening | neutral>",
  "relevance_score": <integer 1-10, where 10 = highly relevant to online safety/AI safety/tech governance>,
  "tags": ["<tag1>", "<tag2>"],
  "implications": "<1 sentence: practical implication for organisations operating in this jurisdiction>"
}}

Urgency guide:
- urgent: new law passed, major enforcement action, significant policy reversal
- notable: new consultation opened, significant guidance issued, major report published
- monitoring: general news, background developments, academic commentary

Item to analyse:
Title: {title}
Source: {source} ({jurisdiction})
URL: {url}
Content: {content}

Return only valid JSON. No markdown, no explanation."""


TREND_SYNTHESIS_PROMPT = """You are analysing {n} recent policy developments across online safety, AI safety, and technology governance.

Jurisdictions: Singapore, Australia, UK, EU, ASEAN.

Here are the items (JSON array):
{items_json}

Write a concise trend synthesis with these sections:

## Key Developments This Period
List the 3-5 most significant items with a 1-sentence description each.

## Emerging Cross-Jurisdiction Trends
Identify 2-3 patterns visible across multiple jurisdictions.

## Regulatory Divergence Points
Where are SG/AU/UK/EU/ASEAN taking meaningfully different approaches?

## Items to Watch
2-3 consultations, reviews, or developments that will likely produce significant outputs soon.

Keep the entire synthesis under 500 words. Be specific — name the instruments, agencies, and jurisdictions involved."""


@dataclass
class AnalysedItem:
    source_id:    str
    title:        str
    url:          str
    published:    str
    jurisdiction: str
    raw_domains:  list[str]

    summary:         str       = ""
    key_points:      list[str] = None
    domain:          str       = "other"
    content_type:    str       = "news"
    urgency:         str       = "monitoring"
    sentiment:       str       = "neutral"
    relevance_score: int       = 5
    tags:            list[str] = None
    implications:    str       = ""
    analysis_error:  str       = ""

    def __post_init__(self):
        if self.key_points is None:
            self.key_points = []
        if self.tags is None:
            self.tags = []


class PolicyAnalyser:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model  = model
        self.usage  = TokenUsage()

    def analyse_item(self, item: RawItem) -> AnalysedItem:
        content = (item.summary or item.raw_text or "(no content available)")[:2000]

        prompt = ITEM_ANALYSIS_PROMPT.format(
            title=item.title,
            source=item.source_id,
            jurisdiction=item.jurisdiction.upper(),
            url=item.url,
            content=content,
        )

        published_str = item.published.isoformat() if item.published else "unknown"
        result = AnalysedItem(
            source_id=item.source_id, title=item.title, url=item.url,
            published=published_str, jurisdiction=item.jurisdiction,
            raw_domains=item.domains,
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            self.usage.add(response)

            raw = response.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            data = json.loads(raw)
            result.summary         = data.get("summary", "")
            result.key_points      = data.get("key_points", [])
            result.domain          = data.get("domain", "other")
            result.content_type    = data.get("content_type", "news")
            result.urgency         = data.get("urgency", "monitoring")
            result.sentiment       = data.get("sentiment", "neutral")
            result.relevance_score = int(data.get("relevance_score", 5))
            result.tags            = data.get("tags", [])
            result.implications    = data.get("implications", "")

        except json.JSONDecodeError as e:
            result.analysis_error = f"JSON parse error: {e}"
        except Exception as e:
            result.analysis_error = f"Analysis error: {e}"

        return result

    def analyse_batch(
        self,
        items: list[RawItem],
        min_relevance: int = 6,
        max_items: int = 100,
    ) -> list[AnalysedItem]:
        results = []
        items = items[:max_items]
        print(f"\n[Analyser] Processing {len(items)} items...")

        for i, item in enumerate(items, 1):
            print(f"  [{i}/{len(items)}] {item.title[:70]}...")
            analysed = self.analyse_item(item)

            if analysed.analysis_error:
                print(f"    ⚠ Error: {analysed.analysis_error}")
                continue
            if analysed.relevance_score < min_relevance:
                print(f"    → Skipped (relevance {analysed.relevance_score} < {min_relevance})")
                continue

            print(f"    ✓ {analysed.urgency.upper()} | score {analysed.relevance_score} | {analysed.domain}")
            results.append(analysed)

        print(f"\n[Analyser] Kept {len(results)} relevant items.")
        print(f"[Analyser] {self.usage.report()}")
        return results

    def synthesise_trends(self, items: list[AnalysedItem]) -> str:
        if not items:
            return "No items to synthesise."

        items_data = [
            {
                "title":        item.title,
                "jurisdiction": item.jurisdiction.upper(),
                "domain":       item.domain,
                "urgency":      item.urgency,
                "summary":      item.summary,
                "published":    item.published,
            }
            for item in items
        ]

        prompt = TREND_SYNTHESIS_PROMPT.format(
            n=len(items),
            items_json=json.dumps(items_data, indent=2),
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            self.usage.add(response)
            return response.content[0].text.strip()
        except Exception as e:
            return f"Trend synthesis failed: {e}"

    def print_cost_report(self):
        """Print final cost summary for the run."""
        print(f"\n{'='*60}")
        print(f"  COST REPORT")
        print(f"{'='*60}")
        print(f"  Model:         {self.model}")
        print(f"  API calls:     {self.usage.api_calls}")
        print(f"  Input tokens:  {self.usage.input_tokens:,}")
        print(f"  Output tokens: {self.usage.output_tokens:,}")
        print(f"  Total tokens:  {self.usage.total_tokens:,}")
        print(f"  Est. cost:     ${self.usage.estimated_cost_usd:.4f} USD")
        print(f"  Monthly est.:  ${self.usage.estimated_cost_usd * 4 * 30:.2f} USD (4 runs/day)")
        print(f"{'='*60}\n")
