# collector/deduplicator.py
"""
Cross-source deduplication before analysis.
Catches same story appearing from both web search and RSS
at different URLs by comparing normalised titles.
"""

import re
from collector.rss_collector import RawItem


def _normalise(title: str) -> str:
    """Lowercase, strip punctuation and common filler words for comparison."""
    title = title.lower()
    title = re.sub(r"[^\w\s]", "", title)          # remove punctuation
    title = re.sub(r"\b(the|a|an|and|or|of|in|on|to|for|with|by)\b", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _similarity(a: str, b: str) -> float:
    """
    Simple word-overlap similarity score between two normalised titles.
    Returns 0.0 (no overlap) to 1.0 (identical).
    """
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0
    overlap = words_a & words_b
    return len(overlap) / max(len(words_a), len(words_b))


def deduplicate(items: list[RawItem], threshold: float = 0.75) -> list[RawItem]:
    """
    Remove near-duplicate items based on title similarity.

    Strategy:
    - RSS/scrape sources take priority over web_search
      (they have richer metadata and exact URLs)
    - Within same source type, first-seen wins
    - Items with similarity >= threshold are considered duplicates

    threshold: 0.75 means 75% word overlap → duplicate
    Lower = more aggressive deduplication
    Higher = only exact matches removed
    """
    # Sort so RSS/scrape items come first — they win over web_search
    sorted_items = sorted(
        items,
        key=lambda x: (0 if x.source_id != "web_search" else 1)
    )

    kept = []
    kept_normalised = []
    removed = 0

    for item in sorted_items:
        norm = _normalise(item.title)
        if not norm:
            kept.append(item)
            kept_normalised.append(norm)
            continue

        is_dup = False
        for existing_norm in kept_normalised:
            if _similarity(norm, existing_norm) >= threshold:
                is_dup = True
                break

        if is_dup:
            removed += 1
        else:
            kept.append(item)
            kept_normalised.append(norm)

    if removed:
        print(f"[Dedup] Removed {removed} near-duplicate items "
              f"(threshold: {threshold}) → {len(kept)} unique items")
    else:
        print(f"[Dedup] No duplicates found → {len(kept)} items")

    return kept
