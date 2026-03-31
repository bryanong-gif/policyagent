# analyser/claude_analyser.py
"""
Claude API analysis layer — fully optimised for token efficiency.

Optimisations:
  #1 — Trusted sources skip pre-score entirely
  #2 — Batch analysis: 5 items per API call
  #3 — URL cache: skip items already in DB
  #4 — Pre-score batch size 20 (was 10)
  #5 — Synthesis only on digest day
"""

import anthropic
import json
import re
from dataclasses import dataclass
from typing import Optional
from collector.rss_collector import RawItem
from collector.prefilter import is_trusted_source

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
    input_tokens:        int = 0
    output_tokens:       int = 0
    api_calls:           int = 0
    haiku_input_tokens:  int = 0
    haiku_output_tokens: int = 0
    haiku_calls:         int = 0

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
        return (_cost(self.sonnet_input_tokens, self.sonnet_output_tokens, "sonnet") +
                _cost(self.haiku_input_tokens,  self.haiku_output_tokens,  "haiku"))

    def report(self) -> str:
        return (
            f"API calls: {self.api_calls} "
            f"(Sonnet: {self.api_calls - self.haiku_calls}, Haiku: {self.haiku_calls}) | "
            f"Tokens: {self.total_tokens:,} | "
            f"Est. cost: ${self.estimated_cost_usd:.4f}"
        )


SYSTEM_PROMPT = """You are a senior policy analyst specialising in:
- Online safety regulation
- AI safety and governance
- Technology policy and data regulation

Jurisdictions: Singapore (sg), Australia (au), UK (uk), EU (eu), ASEAN (asean), global.
Be precise, neutral, and concise. Avoid speculation."""


# Tier 1 — cheap pre-score, batch of 20
PRESCORE_PROMPT = """Rate each item's relevance to online safety, AI safety, or tech governance policy.
Score 1-10. Items scoring 5+ warrant detailed analysis.

Items:
{items_list}

Return ONLY a JSON array of scores in the same order, no explanation:
[score1, score2, ...]"""


# Batch analysis — 5 items per call
BATCH_ANALYSIS_PROMPT = """Analyse each policy/regulatory item and return a JSON array.
One object per item, same order. Each object must have exactly these fields:

{{
  "summary": "<2-3 sentence plain-English summary>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "domain": "<online_safety | ai_safety | tech_governance | other>",
  "content_type": "<legislation | consultation | enforcement | enforcement_action | guidance | academic | news | speech | other>",
  "urgency": "<monitoring | notable | urgent>",
  "sentiment": "<regulatory_tightening | regulatory_loosening | neutral>",
  "relevance_score": <integer 1-10>,
  "tags": ["<tag1>", "<tag2>"],
  "implications": "<1 sentence practical implication>",
  "jurisdiction": "<sg | au | uk | eu | asean | global>"
}}

Urgency: urgent=new law/major enforcement, notable=new consultation/major guidance, monitoring=general news.
Jurisdiction must be one of: sg, au, uk, eu, asean, global. Use "global" for US/other countries.

Items:
{items_json}

Return only a valid JSON array. No markdown, no explanation."""


TREND_SYNTHESIS_PROMPT = """Analyse {n} recent policy developments across online safety, AI safety, and technology governance.
Jurisdictions: Singapore, Australia, UK, EU, ASEAN.

Items:
{items_json}

Write a concise weekly trend synthesis:

## Key Developments This Week
3-5 most significant items, 1 sentence each.

## Cross-Jurisdiction Trends
2-3 patterns visible across multiple jurisdictions this week.

## Regulatory Divergence
Where are SG/AU/UK/EU/ASEAN taking different approaches?

## Items to Watch
2-3 developments likely to produce significant outputs soon.

Under 500 words. Name specific instruments, agencies, jurisdictions."""


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
        self.client     = anthropic.Anthropic(api_key=api_key)
        self.model      = model
        self.fast_model = HAIKU_MODEL
        self.usage      = TokenUsage()

    def filter_seen_urls(self, items: list[RawItem], db) -> list[RawItem]:
        """#3 URL cache — skip already-stored items."""
        unseen = [i for i in items if not db.item_exists(i.url)]
        cached = len(items) - len(unseen)
        if cached:
            print(f"[URL Cache] Skipped {cached} already-stored URLs → {len(unseen)} new items")
        return unseen

    def prescore(self, items: list[RawItem], threshold: int = 4) -> list[RawItem]:
        """
        #4 Pre-score with batch size 20.
        Trusted sources bypass pre-scoring entirely.
        """
        if not items:
            return []

        # Split trusted (bypass) vs untrusted (needs pre-score)
        trusted   = [i for i in items if is_trusted_source(i.source_id) or i.source_id.startswith("web_search")]
        untrusted = [i for i in items if not is_trusted_source(i.source_id) and not i.source_id.startswith("web_search")]

        passing = list(trusted)  # trusted sources always pass

        if untrusted:
            batch_size = 20  # increased from 10 — halves pre-score API calls
            for i in range(0, len(untrusted), batch_size):
                batch = untrusted[i:i + batch_size]
                items_list = "\n".join(
                    f"{j+1}. [{item.jurisdiction.upper()}] {item.title}"
                    for j, item in enumerate(batch)
                )
                try:
                    response = self.client.messages.create(
                        model=self.fast_model,
                        max_tokens=150,
                        system=SYSTEM_PROMPT,
                        messages=[{"role": "user", "content": PRESCORE_PROMPT.format(items_list=items_list)}],
                    )
                    self.usage.add(response, model=self.fast_model)
                    raw = response.content[0].text.strip().replace("```json","").replace("```","").strip()
                    scores = json.loads(raw)
                    for item, score in zip(batch, scores):
                        if int(score) > threshold:
                            passing.append(item)
                        else:
                            print(f"  [Pre-score {score}] Skipped: {item.title[:60]}")
                except Exception as e:
                    print(f"  [WARN] Pre-score failed, passing batch: {e}")
                    passing.extend(batch)

        skipped = len(items) - len(passing)
        print(f"[Pre-score] Passed: {len(passing)} / {len(items)} "
              f"(trusted: {len(trusted)}, skipped: {skipped})")
        return passing

    def analyse_batch_items(self, items: list[RawItem], batch_size: int = 5) -> list[AnalysedItem]:
        """#2 Batch analysis — 5 items per API call."""
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
                    "content":      (item.summary or item.raw_text or "")[:600],
                }
                for j, item in enumerate(batch)
            ]

            print(f"  [Batch {i//batch_size + 1}] Analysing {len(batch)} items in one call...")

            try:
                response = self.client.messages.create(
                    model=self.fast_model,
                    max_tokens=batch_size * 350,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": BATCH_ANALYSIS_PROMPT.format(
                        items_json=json.dumps(items_data, indent=2)
                    )}],
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
                    # Use Claude's jurisdiction if it provided one
                    claude_jur = data.get("jurisdiction", "").lower().strip()
                    valid_jurs = {"sg","au","uk","eu","asean","global"}
                    result.jurisdiction    = claude_jur if claude_jur in valid_jurs else item.jurisdiction
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
                print(f"  [WARN] Batch JSON parse error: {e} — skipping batch")
            except Exception as e:
                print(f"  [WARN] Batch error: {e}")

        return results

    def analyse_batch(
        self,
        items: list[RawItem],
        min_relevance: int = 6,
        max_items: int = 100,
        db=None,
    ) -> list[AnalysedItem]:
        """Full pipeline: URL cache → pre-score → batch analysis → filter."""
        items = items[:max_items]
        print(f"\n[Analyser] {len(items)} items entering pipeline...")

        if db:
            items = self.filter_seen_urls(items, db)
        if not items:
            print("[Analyser] All items already stored.")
            return []

        items = self.prescore(items, threshold=4)
        if not items:
            print("[Analyser] No items passed pre-score.")
            return []

        print(f"\n[Analyser] Full analysis on {len(items)} items...")
        analysed = self.analyse_batch_items(items, batch_size=5)

        results = [a for a in analysed if a.relevance_score >= min_relevance]
        skipped = len(analysed) - len(results)
        print(f"\n[Analyser] Kept {len(results)} items (filtered {skipped} below score {min_relevance})")
        print(f"[Analyser] {self.usage.report()}")
        return results

    def synthesise_trends(self, items) -> str:
        """#5 Synthesis — Sonnet, called only on digest day."""
        if not items:
            return "No items to synthesise."

        items_data = [
            {
                "title":        getattr(item, "title", str(item)),
                "jurisdiction": getattr(item, "jurisdiction", "global"),
                "domain":       getattr(item, "domain", "other"),
                "urgency":      getattr(item, "urgency", "monitoring"),
                "summary":      getattr(item, "summary", ""),
            }
            for item in items
        ]

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=800,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": TREND_SYNTHESIS_PROMPT.format(
                    n=len(items),
                    items_json=json.dumps(items_data, indent=2),
                )}],
            )
            self.usage.add(response, model=self.model)
            return response.content[0].text.strip()
        except Exception as e:
            return f"Trend synthesis failed: {e}"

    def print_cost_report(self):
        runs_per_month = 30
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
