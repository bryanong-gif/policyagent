# collector/prefilter.py
"""
Optimisation #1 — Keyword pre-filter.
Drops obviously irrelevant items before they hit the Claude API.
Saves ~20-30% of analysis calls at zero cost.
"""

import re
from collector.rss_collector import RawItem

# Must contain at least one of these to pass
MUST_INCLUDE = [
    "regulat", "policy", "law", "act", "bill", "legislation", "directive",
    "enforcement", "compliance", "govern", "safety", "privacy", "data",
    "digital", "cyber", "AI", "artificial intelligence", "online",
    "platform", "content", "surveillance", "biometric", "algorithm",
    "accountability", "transparency", "risk", "harm", "misinformation",
    "disinformation", "trust", "rights", "framework", "guideline",
    "consultation", "parliament", "congress", "senate", "commission",
    "authority", "agency", "minister", "court", "ruling", "fine",
    "penalty", "sanction", "investigation", "probe", "review",
]

# Drop if title/summary contains any of these
EXCLUDE_PATTERNS = [
    r"\bjob\b", r"\bcareer\b", r"\bhiring\b", r"\bvacancy\b",
    r"\bevent\b", r"\bconference\b", r"\bwebinar\b", r"\bworkshop\b",
    r"\baward\b", r"\bwinner\b", r"\bsponsored\b", r"\badvertis",
    r"\bstock\b", r"\bshare price\b", r"\bearnings\b", r"\brevenue\b",
    r"\bproduct launch\b", r"\bnew feature\b", r"\bupdate available\b",
    r"\bholiday\b", r"\bfestival\b", r"\brecipe\b", r"\bsport\b",
    r"\bfootball\b", r"\bbasketball\b", r"\btennis\b", r"\bgolf\b",
]

# Compile exclude patterns once
_EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)


def prefilter(items: list[RawItem]) -> list[RawItem]:
    """
    Keep items that:
    1. Contain at least one policy/regulatory keyword
    2. Don't match any exclusion pattern
    """
    kept = []
    dropped = 0

    for item in items:
        text = f"{item.title} {item.summary}".lower()

        # Check exclusions first (fast path)
        if _EXCLUDE_RE.search(text):
            dropped += 1
            continue

        # Must match at least one include keyword
        if any(kw.lower() in text for kw in MUST_INCLUDE):
            kept.append(item)
        else:
            dropped += 1

    if dropped:
        print(f"[Prefilter] Dropped {dropped} irrelevant items → {len(kept)} remain")
    else:
        print(f"[Prefilter] All {len(kept)} items passed")

    return kept
