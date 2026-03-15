# analyser/claude_analyser.py
"""
Claude API analysis layer — fully optimised for token efficiency.

Optimisations:
  #2 — Batch analysis: 5 items per API call instead of 1
  #3 — URL cache: skip items already in DB from recent runs
  #4 — Tiered analysis: cheap pre-score first, full analysis only for passing items
  #5 — Synthesis only on digest day
"""

import anthropic
import json
import re
from dataclasses import dataclass
from typing import Optional
from collector.rss_collector import RawItem

# Pricing per million tokens
SONNET_INPUT_COST_PER_M  = 3.00
SONNET_OUTPUT_COST_PER_M = 15.00
HAIKU_INPUT_COST_PER_M   = 0.80
HAIKU_OUTPUT_COST_PER_M  = 4.00

HAIKU_MODEL  = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-20250514"


def _cost(input_tokens: int, output_tokens: int, model: str = "sonnet") -> float:
    if "haiku" in model:
        return (input_tokens / 1_000_000 * HAIKU_INPUT_COST_PER_M +
                output_tokens / 1_000_000 * HAIKU_OUTPUT_COST_PER_M)
    return (input_tokens / 1_000_000 * SONNET_INPUT_COST_PER_M +
            output_tokens / 1_000_000 * SONNET_OUTPUT_COST_PER_M)


@dataclass
class TokenUsage:
    input_tokens:        int   = 0
    output_tokens:       int   = 0
    api_calls:           int   = 0
    haiku_input_tokens:  int   = 0
    haiku_output_tokens: int   = 0
    haiku_calls:         int   = 0

    def add(self, response, model: str = "sonnet"):
        if hasattr(response, "usage") and response.usage:
            inp = getattr(response.usage, "input_tokens",  0)
            out = getattr(response.usage, "output_tokens", 0)
            self.input_tokens  += inp
            self.output_tokens += out
            if "haiku" in model:
                self.haiku_input_tokens  += inp
                self.haiku_output_tokens += out
                self.haiku_calls         += 1
        self.api_calls += 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def sonnet_input_tokens(self) -> int:
        return self.input_tokens - self.haiku_input_tokens

    @property
    def sonnet_output_tokens(self) -> int:
        return self.output_tokens - self.haiku_output_tokens

    @property
    def estimated_cost_usd(self) -> float:
        sonnet_cost = _cost(self.sonnet_input_tokens, self.sonnet_output_tokens, "sonnet")
        haiku_cost  = _cost(self.haiku_input_tokens,  self.haiku_output_tokens,  "haiku")
        return sonnet_cost + haiku_cost

    def report(self) -> str:
        return (
            f"API calls: {self.api_calls} (Sonnet: {self.api_calls - self.haiku_calls}, Haiku: {self.haiku_calls}) | "
            f"Tokens: {self.total_tokens:,} | "
            f"Est. cost: ${self.estimated_cost_usd:.4f}"
        )


SYSTEM_PROMPT = """You are a senior policy analyst specialising in:
- Online safety regulation
- AI safety and governance
- Technology policy and data regulation

Your role is to analyse policy-related content from five jurisdictions:
Singapore (sg), Australia (au), United Kingdom (uk), European Union (eu), ASEAN (asean), and global.

Be precise, neutral, and concise. Avoid speculation. Flag when something is genuinely significant."""


# ── Optimisation #4: Tier 1 — cheap pre-score prompt ─────────────────────────
# Sends up to 10 items in one call, returns just a relevance score per item.
# Items scoring below threshold skip full analysis entirely.
PRESCORE_PROMPT = """Rate each item's relevance to online safety, AI safety, or tech governance policy.
Score 1-10. Only items scoring 6+ warrant detailed analysis.

Items:
{items_list}

Return ONLY a JSON array of scores in the same order, no explanation:
[score1, score2, ...]"""


# ── Optimisation #2: Batch analysis prompt ────────────────────────────────────
# Analyses 5 items in one API call instead of 5 separate calls.
BATCH_ANALYSIS_PROMPT = """Analyse each policy/regulatory item below and return a JSON array.
One object per item, in the same order. Each object must have exactly these fields:

{{
  "summary": "<2-3 sentence plain-English summary>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "domain": "<online_safety | ai_safety | tech_governance | other>",
  "content_type": "<legislation | consultation | enforcement | enforcement_action | guidance | academic | news | speech | other>",
  "urgency": "<monitoring | notable | urgent>",
  "sentiment": "<regulatory_tightening | regulatory_loosening | neutral>",
  "relevance_score": <integer 1-10>,
  "tags": ["<tag1>", "<tag2>"],
  "implications": "<1 sentence practical implication>"
}}

Urgency: urgent=new law/major enforcement, notable=new consultation/major guidance, monitoring=general news.

Items to analyse:
{items_json}

Return only a valid JSON array. No markdown, no explanation."""


TREND_SYNTHESIS_PROMPT = """You are analysing {n} recent policy developments across online safety, AI safety, and technology governance.

Jurisdictions: Singapore, Australia, UK, EU, ASEAN.

Items:
{items_json}

Write a concise trend synthesis with these sections:

## Key Developments This Period
3-5 most significant items, 1 sentence each.

## Emerging Cross-Jurisdiction Trends
2-3 patterns visible across multiple jurisdictions.

## Regulatory Divergence Points
Where are SG/AU/UK/EU/ASEAN taking meaningfully different approaches?

## Items to Watch
2-3 developments likely to produce significant outputs soon.

Under 500 words. Name specific instruments, agencies, and jurisdictions."""


@dataclass
class AnalysedItem:
    source_id:    str
    title:        str
    url:          str
    published:    str
    jurisdiction: str
    raw_domains:  list

    summary:         str  = ""
    key_points:      list = None
    domain:          str  = "other"
    content_type:    str  = "news"
    urgency:         str  = "monitoring"
    sentiment:       str  = "neutral"
    relevance_score: int  = 5
    tags:            list = None
    implications:    str  = ""
    analysis_error:  str  = ""

    def __post_init__(self):
        if self.key_points is None: self.key_points = []
        if self.tags is None:       self.tags = []


class PolicyAnalyser:
    def __init__(self, api_key: str, model: str = SONNET_MODEL):
        self.client       = anthropic.Anthropic(api_key=api_key)
        self.model        = model        # Sonnet — used for synthesis only
        self.fast_model   = HAIKU_MODEL  # Haiku — used for pre-score + batch analysis
        self.usage        = TokenUsage()

    # ── Optimisation #3: URL cache ────────────────────────────────────────────
    def filter_seen_urls(self, items: list[RawItem], db) -> list[RawItem]:
        """Remove items whose URLs are already in the database (cross-run dedup)."""
        unseen = [i for i in items if not db.item_exists(i.url)]
        cached = len(items) - len(unseen)
        if cached:
            print(f"[URL Cache] Skipped {cached} already-stored URLs → {len(unseen)} new items")
        return unseen

    # ── Optimisation #4: Tier 1 pre-score ────────────────────────────────────
    def prescore(self, items: list[RawItem], threshold: int = 5) -> list[RawItem]:
        """
        Cheap pre-score: send up to 10 items in one call, get a score per item.
        Only items scoring > threshold proceed to full analysis.
        Saves full analysis calls on low-relevance items.
        """
        if not items:
            return []

        passing = []
        batch_size = 10

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            items_list = "\n".join(
                f"{j+1}. [{item.jurisdiction.upper()}] {item.title}"
                for j, item in enumerate(batch)
            )
            prompt = PRESCORE_PROMPT.format(items_list=items_list)

            try:
                response = self.client.messages.create(
                    model=self.fast_model,
                    max_tokens=200,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                self.usage.add(response, model=self.fast_model)
                raw = response.content[0].text.strip()
                raw = raw.replace("```json","").replace("```","").strip()
                scores = json.loads(raw)

                for item, score in zip(batch, scores):
                    if int(score) > threshold:
                        passing.append(item)
                    else:
                        print(f"  [Pre-score {score}] Skipped: {item.title[:60]}")

            except Exception as e:
                print(f"  [WARN] Pre-score failed, passing all items: {e}")
                passing.extend(batch)

        skipped = len(items) - len(passing)
        print(f"[Pre-score] Passed: {len(passing)} / {len(items)} "
              f"(skipped {skipped} low-relevance items)")
        return passing

    # ── Optimisation #2: Batch analysis ──────────────────────────────────────
    def analyse_batch_items(self, items: list[RawItem], batch_size: int = 5) -> list[AnalysedItem]:
        """
        Analyse items in batches of batch_size (default 5) per API call.
        5x fewer API calls than one-at-a-time.
        """
        results = []

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]
            items_data = [
                {
                    "index":        j + 1,
                    "title":        item.title,
                    "source":       item.source_id,
                    "jurisdiction": item.jurisdiction.upper(),
                    "url":          item.url,
                    "content":      (item.summary or item.raw_text or "")[:800],
                }
                for j, item in enumerate(batch)
            ]

            prompt = BATCH_ANALYSIS_PROMPT.format(
                items_json=json.dumps(items_data, indent=2)
            )

            print(f"  [Batch {i//batch_size + 1}] Analysing {len(batch)} items in one call...")

            try:
                response = self.client.messages.create(
                    model=self.fast_model,
                    max_tokens=batch_size * 350,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                self.usage.add(response, model=self.fast_model)

                raw = response.content[0].text.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                analysed_data = json.loads(raw)

                for item, data in zip(batch, analysed_data):
                    published_str = item.published.isoformat() if item.published else "unknown"
                    result = AnalysedItem(
                        source_id=item.source_id, title=item.title,
                        url=item.url, published=published_str,
                        jurisdiction=item.jurisdiction, raw_domains=item.domains,
                    )
                    result.summary         = data.get("summary", "")
                    result.key_points      = data.get("key_points", [])
                    result.domain          = data.get("domain", "other")
                    result.content_type    = data.get("content_type", "news")
                    result.urgency         = data.get("urgency", "monitoring")
                    result.sentiment       = data.get("sentiment", "neutral")
                    result.relevance_score = int(data.get("relevance_score", 5))
                    result.tags            = data.get("tags", [])
                    result.implications    = data.get("implications", "")
                    results.append(result)
                    print(f"    ✓ {result.urgency.upper()} | {result.relevance_score}/10 | {result.title[:50]}")

            except json.JSONDecodeError as e:
                print(f"  [WARN] Batch JSON parse error: {e} — falling back to individual analysis")
                for item in batch:
                    result = self._analyse_single(item)
                    if not result.analysis_error:
                        results.append(result)
            except Exception as e:
                print(f"  [WARN] Batch error: {e}")

        return results

    def _analyse_single(self, item: RawItem) -> AnalysedItem:
        """Fallback: analyse a single item (used when batch parsing fails)."""
        SINGLE_PROMPT = """Analyse this item and return a JSON object:
{{
  "summary": "...", "key_points": [], "domain": "...", "content_type": "...",
  "urgency": "...", "sentiment": "...", "relevance_score": 5, "tags": [], "implications": "..."
}}
Title: {title} | Source: {source} ({jurisdiction}) | Content: {content}
Return only JSON."""
        content = (item.summary or "")[:600]
        published_str = item.published.isoformat() if item.published else "unknown"
        result = AnalysedItem(
            source_id=item.source_id, title=item.title, url=item.url,
            published=published_str, jurisdiction=item.jurisdiction, raw_domains=item.domains,
        )
        try:
            response = self.client.messages.create(
                model=self.fast_model, max_tokens=400, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": SINGLE_PROMPT.format(
                    title=item.title, source=item.source_id,
                    jurisdiction=item.jurisdiction.upper(), content=content
                )}],
            )
            self.usage.add(response, model=self.fast_model)
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
        except Exception as e:
            result.analysis_error = str(e)
        return result

    def analyse_batch(
        self,
        items: list[RawItem],
        min_relevance: int = 6,
        max_items: int = 100,
        db=None,
    ) -> list[AnalysedItem]:
        """
        Full pipeline:
        #3 URL cache → #4 pre-score → #2 batch analysis → relevance filter
        """
        items = items[:max_items]
        print(f"\n[Analyser] {len(items)} items entering pipeline...")

        # #3 URL cache — skip already-stored items
        if db:
            items = self.filter_seen_urls(items, db)

        if not items:
            print("[Analyser] All items already stored — nothing to analyse.")
            return []

        # #4 Pre-score — drop low-relevance items cheaply
        items = self.prescore(items, threshold=4)

        if not items:
            print("[Analyser] No items passed pre-score.")
            return []

        # #2 Batch analysis — 5 items per call
        print(f"\n[Analyser] Full analysis on {len(items)} items...")
        analysed = self.analyse_batch_items(items, batch_size=5)

        # Final relevance filter
        results = [a for a in analysed if a.relevance_score >= min_relevance]
        skipped = len(analysed) - len(results)

        print(f"\n[Analyser] Kept {len(results)} items "
              f"(filtered {skipped} below score {min_relevance})")
        print(f"[Analyser] {self.usage.report()}")
        return results

    # ── Optimisation #5: Synthesis only on digest day ─────────────────────────
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
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            self.usage.add(response, model=self.model)
            return response.content[0].text.strip()
        except Exception as e:
            return f"Trend synthesis failed: {e}"

    def print_cost_report(self):
        runs_per_month = 2 * 30
        u = self.usage
        print(f"\n{'='*60}")
        print(f"  COST REPORT")
        print(f"{'='*60}")
        print(f"  Sonnet calls:  {u.api_calls - u.haiku_calls} | tokens: {u.sonnet_input_tokens + u.sonnet_output_tokens:,}")
        print(f"  Haiku calls:   {u.haiku_calls} | tokens: {u.haiku_input_tokens + u.haiku_output_tokens:,}")
        print(f"  Total calls:   {u.api_calls}")
        print(f"  Total tokens:  {u.total_tokens:,}")
        print(f"  Est. cost:     ${u.estimated_cost_usd:.4f} USD")
        print(f"  Monthly est.:  ${u.estimated_cost_usd * runs_per_month:.2f} USD ({runs_per_month} runs/month)")
        print(f"{'='*60}\n")
