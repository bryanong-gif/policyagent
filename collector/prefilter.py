# collector/prefilter.py
"""
Optimisation #1 — Keyword pre-filter.
Drops obviously irrelevant items before they hit the Claude API.
Also tags items from trusted sources to skip pre-scoring.
"""

import re
from collector.rss_collector import RawItem

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

EXCLUDE_PATTERNS = [
    r"\bjob\b", r"\bcareer\b", r"\bhiring\b", r"\bvacancy\b",
    r"\bevent\b", r"\bconference\b", r"\bwebinar\b", r"\bworkshop\b",
    r"\baward\b", r"\bwinner\b", r"\bsponsored\b", r"\badvertis",
    r"\bstock\b", r"\bearnings\b", r"\brevenue\b",
    r"\bproduct launch\b", r"\bnew feature\b",
    r"\bholiday\b", r"\bfestival\b", r"\brecipe\b",
    r"\bfootball\b", r"\bbasketball\b", r"\btennis\b", r"\bgolf\b",
    r"\bcricket\b", r"\bmessi\b", r"\bonfield\b",
    r"\bfilm\b", r"\bmovie\b", r"\bmusic\b", r"\bconcert\b",
    r"\boil price\b", r"\bcrude\b", r"\bopec\b",
    r"\bwar\b.*\boil\b", r"\biran\b.*\battack\b",
]

_EXCLUDE_RE = re.compile("|".join(EXCLUDE_PATTERNS), re.IGNORECASE)

# Source IDs that are always trusted — skip pre-score entirely
TRUSTED_SOURCE_PREFIXES = (
    "sg_imda", "sg_pdpc", "sg_mas", "sg_csa", "sg_govtech", "sg_mddi",
    "sg_rsis", "sg_iseas", "sg_data_privacy", "sg_iapp",
    "au_acma", "au_oaic",
    "uk_ofcom", "uk_ico", "uk_dsit", "uk_aisi",
    "eu_commission", "eu_edpb", "eu_enisa", "eu_parliament",
    "eu_ai_act", "eu_access_now", "eu_algorithmwatch", "eu_edri",
    "eu_digitaleurope", "eu_dsa_tracker", "eu_ai_office",
    "asean_secretariat", "cf_internet",
    "oecd_ai", "ada_lovelace", "ai_now", "tech_policy_press",
    "future_of_life",
)


def is_trusted_source(source_id: str) -> bool:
    return any(source_id.startswith(p) for p in TRUSTED_SOURCE_PREFIXES)


def prefilter(items: list[RawItem]) -> list[RawItem]:
    kept = []
    dropped = 0

    for item in items:
        # Trusted gov/think tank sources — pass through without keyword check
        if is_trusted_source(item.source_id):
            kept.append(item)
            continue

        text = f"{item.title} {item.summary}".lower()

        # Exclusion check
        if _EXCLUDE_RE.search(text):
            dropped += 1
            continue

        # Must include at least one policy keyword
        if any(kw.lower() in text for kw in MUST_INCLUDE):
            kept.append(item)
        else:
            dropped += 1

    if dropped:
        print(f"[Prefilter] Dropped {dropped} irrelevant items → {len(kept)} remain")
    else:
        print(f"[Prefilter] All {len(kept)} items passed")

    return kept
