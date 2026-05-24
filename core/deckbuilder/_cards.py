"""Card property helpers: legality, color identity, themes, curve, type grouping."""
import json
from collections import Counter
from typing import Optional

from ._constants import THEMES, _TRIBAL_TYPES


def is_legal(card: dict, fmt: str) -> bool:
    leg = card.get("legalities") or {}
    if isinstance(leg, str):
        try:
            leg = json.loads(leg)
        except Exception:
            return False
    return leg.get(fmt) == "legal"


def _is_pool_eligible(card: dict, fmt: str) -> bool:
    """Whether a card belongs in a format's pool.

    Like is_legal for all formats except vintage, where restricted cards
    (max 1 copy) are still playable.
    """
    if fmt != "vintage":
        return is_legal(card, fmt)
    leg = card.get("legalities") or {}
    if isinstance(leg, str):
        try:
            leg = json.loads(leg)
        except Exception:
            return False
    return leg.get("vintage") in ("legal", "restricted")


def _max_copies(card: dict, fmt: str) -> int:
    """Hard copy-count cap for this card in this format (before availability limits)."""
    if fmt == "vintage":
        leg = card.get("legalities") or {}
        if isinstance(leg, str):
            try:
                leg = json.loads(leg)
            except Exception:
                return 4
        if leg.get("vintage") == "restricted":
            return 1
    return 4


def color_identity(card: dict) -> frozenset:
    ci = card.get("color_identity") or []
    if isinstance(ci, str):
        try:
            ci = json.loads(ci)
        except Exception:
            ci = []
    return frozenset(ci)


def get_card_themes(card: dict) -> set[str]:
    text = (card.get("oracle_text") or "").lower()
    tl   = (card.get("type_line")   or "").lower()
    themes: set[str] = set()
    for theme, kws in THEMES.items():
        if any(kw in text for kw in kws):
            themes.add(theme)
    for tribe in _TRIBAL_TYPES:
        t = tribe.lower()
        if t in tl or f"{t}s" in text or f"each {t}" in text or f"other {t}" in text:
            themes.add(f"tribal_{t}")
    return themes


def is_commander_eligible(card: dict) -> bool:
    tl     = card.get("type_line")   or ""
    oracle = card.get("oracle_text") or ""
    return (
        "Legendary Creature" in tl
        or ("Legendary" in tl and "can be your commander" in oracle)
    )


def _type_group(card: dict) -> str:
    tl = card.get("type_line") or ""
    for token, label in (
        ("Creature",     "Creatures"),
        ("Planeswalker", "Planeswalkers"),
        ("Instant",      "Instants"),
        ("Sorcery",      "Sorceries"),
        ("Enchantment",  "Enchantments"),
        ("Artifact",     "Artifacts"),
        ("Land",         "Lands"),
    ):
        if token in tl:
            return label
    return "Other"


def curve_analysis(nonland_cards: list[tuple[dict, int]]) -> dict[int, int]:
    buckets: dict[int, int] = {}
    for card, qty in nonland_cards:
        cmc = int(card.get("cmc") or 0)
        bucket = min(cmc, 6)
        buckets[bucket] = buckets.get(bucket, 0) + qty
    return buckets


def _dedup_physical(cards: list[dict]) -> list[dict]:
    """Remove entries with duplicate DB IDs, keeping the first occurrence.

    Cards with no ``id`` (e.g. missing basics) are always kept.
    """
    seen: set = set()
    result: list[dict] = []
    for c in cards:
        card_id = c.get("id")
        if card_id is None or card_id not in seen:
            result.append(c)
            if card_id is not None:
                seen.add(card_id)
    return result


def get_available_strategies(pool: list[dict]) -> list[tuple[str, str, int]]:
    """Return (key, display_label, card_count) triples for every theme present in pool."""
    theme_hits: Counter = Counter()
    for c in pool:
        for t in get_card_themes(c):
            theme_hits[t] += 1
    return [
        (t, t.replace("tribal_", "").title(), theme_hits[t])
        for t, _ in theme_hits.most_common()
        if theme_hits[t] > 0
    ]
