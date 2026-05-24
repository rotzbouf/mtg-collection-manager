"""60-card deck builder for standard, modern, legacy, vintage, pauper."""
from collections import Counter
from typing import Optional

from ._cards import (
    _is_pool_eligible, _max_copies, color_identity,
    get_card_themes, curve_analysis,
)
from ._roles import tag_card_roles, _fill_role_slots
from ._mana import _build_land_base
from ._pool import _apply_power_level_filter, _enforce_diversity
from ._constants import _60_ROLE_TARGETS, _THEME_TO_ARCH, _ARCH_TO_THEME


_AFFINITY_WEIGHT = 18.0  # bonus per deck the card appears in
_META_WEIGHT     = 0.25  # meta score 0–100; score 100 → +25 pts, score 50 → +12.5 pts
_ANCHOR_MIN_DECKS = 2   # cards in this many user decks are pre-selected as anchors
_ANCHOR_MAX_SLOTS = 8   # at most this many anchor card slots


def _build_60_core(
    pool: list[dict],
    fmt: str,
    archetype: str,
    theme_key: Optional[str],
    power_level: str,
    max_price: Optional[float],
    deck_affinity: Optional[dict[str, float]] = None,
    meta_scores: Optional[dict[str, float]] = None,
) -> dict:
    """Build a single 60-card deck for one archetype (no variants)."""
    from core.analysis import select_for_curve, deck_synergy_score, score_card, detect_archetypes

    affinity = deck_affinity or {}
    meta     = meta_scores or {}

    def _score(c: dict) -> float:
        name = (c.get("name_en") or "").lower()
        return (
            score_card(c, fmt)
            + affinity.get(name, 0.0) * _AFFINITY_WEIGHT
            + meta.get(name, 0.0) * _META_WEIGHT
        )

    legal_nonland = _apply_power_level_filter(
        [c for c in pool if _is_pool_eligible(c, fmt) and "Land" not in (c.get("type_line") or "")],
        power_level, max_price,
    )

    available: Counter = Counter()
    name_first: dict[str, dict] = {}
    name_all: dict[str, list[dict]] = {}  # all physical copies, preserving container info
    for card in legal_nonland:
        name = (card.get("name_en") or "").lower()
        available[name] += 1
        if name not in name_first:
            name_first[name] = card
        name_all.setdefault(name, []).append(card)

    unique_nonland = sorted(
        name_first.values(), key=_score, reverse=True
    )

    target_nonland = 36
    target_lands   = 24

    chosen_names: set[str] = set()
    role_cards, _ = _fill_role_slots(unique_nonland, dict(_60_ROLE_TARGETS), chosen_names)

    # Anchor cards: consistently used across the user's existing decks of this format.
    # These are pre-selected alongside role cards so the builder always includes them.
    anchor_cards: list[dict] = []
    if affinity:
        for nm, aff_cnt in sorted(affinity.items(), key=lambda x: x[1], reverse=True):
            if len(anchor_cards) >= _ANCHOR_MAX_SLOTS:
                break
            if aff_cnt < _ANCHOR_MIN_DECKS:
                break
            if nm in chosen_names or nm not in name_first:
                continue
            anchor_cards.append(name_first[nm])
            chosen_names.add(nm)

    theme_keys = {theme_key} if theme_key else set()
    if not theme_keys:
        theme_hits: Counter = Counter()
        for c in legal_nonland:
            for t in get_card_themes(c):
                theme_hits[t] += 1
        if theme_hits:
            theme_keys = {theme_hits.most_common(1)[0][0]}

    remaining_unique = [c for c in unique_nonland if (c.get("name_en") or "").lower() not in chosen_names]
    themed     = [c for c in remaining_unique if theme_keys and any(t in get_card_themes(c) for t in theme_keys)]
    others     = [c for c in remaining_unique if c not in themed]
    candidates = themed + others

    remaining_target = max(1, target_nonland - len(role_cards) - len(anchor_cards))
    theme_cards = select_for_curve(candidates, archetype, remaining_target, fmt="60",
                                   meta_scores=meta_scores)

    selected_unique = list(role_cards) + anchor_cards
    sel_names: set[str] = {(c.get("name_en") or "").lower() for c in selected_unique}
    for c in theme_cards:
        name = (c.get("name_en") or "").lower()
        if name not in sel_names and len(selected_unique) < target_nonland:
            selected_unique.append(c)
            sel_names.add(name)

    all_unique = list(name_first.values())
    selected_unique = _enforce_diversity(selected_unique, archetype, all_unique, target_nonland, "60")

    deck_cards: list[tuple[dict, int]] = []
    deck_physical: list[dict] = []  # individual physical copies with correct container attribution
    total = 0
    for card in selected_unique:
        if total >= target_nonland:
            break
        name = (card.get("name_en") or "").lower()
        take = min(available[name], _max_copies(card, fmt), target_nonland - total)
        if take > 0:
            deck_cards.append((card, take))
            deck_physical.extend(name_all[name][:take])
            total += take

    colors_used: set[str] = set()
    for card, _ in deck_cards:
        colors_used |= color_identity(card)
    ci = frozenset(colors_used)

    # If the pool didn't fill all nonland slots, pad with extra basics so the
    # total always reaches 60 (e.g. 28 spells → 32 lands instead of 24).
    actual_nonland  = sum(n for _, n in deck_cards)
    nonland_shortfall = max(0, target_nonland - actual_nonland)
    adjusted_lands  = target_lands + nonland_shortfall

    nonland_for_pips = [c for c, _ in deck_cards]
    land_base = _build_land_base(pool, ci, nonland_for_pips, adjusted_lands, fmt)
    nonbasic_lands         = land_base["nonbasic_lands"]
    basics_from_collection = land_base["basics_from_collection"]
    basics_missing         = land_base["basics_missing"]

    role_summary: Counter = Counter()
    for c, _ in deck_cards:
        for r in tag_card_roles(c):
            role_summary[r] += 1

    collection_count = (
        sum(n for _, n in deck_cards)
        + len(nonbasic_lands)
        + len(basics_from_collection)
    )
    missing_basics_count = sum(basics_missing.values()) if basics_missing else 0
    total_cards = collection_count + missing_basics_count   # always 60

    value = round(
        sum((c.get("price_eur") or 0) * n for c, n in deck_cards)
        + sum(c.get("price_eur") or 0 for c in nonbasic_lands),
        2,
    )

    return {
        "deck":                   deck_cards,
        "deck_physical":          deck_physical,
        "nonbasic_lands":         nonbasic_lands,
        "basics_from_collection": basics_from_collection,
        "basics_missing":         basics_missing,
        "padding_basics":         nonland_shortfall,
        "strategy":               (theme_key or "").replace("tribal_", "").title() or archetype,
        "format":                 fmt,
        "collection_count":       collection_count,
        "total_cards":            total_cards,
        "value_eur":              value,
        "curve":                  curve_analysis(deck_cards),
        "archetype":              archetype,
        "archetypes":             detect_archetypes([c for c, _ in deck_cards])[:3] if deck_cards else [],
        "synergy_score":          deck_synergy_score([c for c, _ in deck_cards[:30]]),
        "role_summary":           dict(role_summary),
        "power_level":            power_level,
    }


def build_60_deck(
    pool: list[dict],
    fmt: str,
    forced_strategy: Optional[str] = None,
    power_level: str = "focused",
    max_price: Optional[float] = None,
    deck_affinity: Optional[dict[str, float]] = None,
    meta_scores: Optional[dict[str, float]] = None,
) -> dict:
    """Build a 60-card deck for the given format.

    Supports standard, modern, legacy, vintage, and pauper.
    Always attempts to return up to 3 archetype variants (primary + alternatives).

    meta_scores: optional {card_name_lower: 0–100} from the competitive meta
                 DB; higher-scoring cards get a modest scoring bonus.
    """
    from core.analysis import detect_archetypes, meta_preferred_archetype

    legal_nonland = [
        c for c in pool
        if _is_pool_eligible(c, fmt) and "Land" not in (c.get("type_line") or "")
    ]

    detected = detect_archetypes(legal_nonland)

    if forced_strategy:
        forced_arch = _THEME_TO_ARCH.get(forced_strategy, forced_strategy.replace("tribal_", "").title())
        # Put forced archetype first, then detected ones as alternatives.
        alts = [(a, c) for a, c in detected if a != forced_arch]
        archetypes = [(forced_arch, 1.0)] + alts[:2]
    else:
        archetypes = detected or [("Midrange", 1.0)]
        # If meta data is available, nudge the primary archetype toward what the
        # competitive meta favours (only when the collection doesn't already
        # strongly point to one archetype).
        if meta_scores and (not detected or detected[0][1] < 0.70):
            meta_arch = meta_preferred_archetype(meta_scores, legal_nonland)
            if meta_arch and meta_arch != archetypes[0][0]:
                # Insert meta-preferred as primary, original primary as 2nd
                existing = [(a, c) for a, c in archetypes if a != meta_arch]
                archetypes = [(meta_arch, 1.0)] + existing[:2]

    variants: list[dict] = []
    for arch, conf in archetypes[:3]:
        if conf < 0.15 and variants:
            break
        theme = forced_strategy if (forced_strategy and not variants) else _ARCH_TO_THEME.get(arch)
        result = _build_60_core(pool, fmt, arch, theme, power_level, max_price, deck_affinity, meta_scores)
        result["archetype_confidence"] = round(conf, 3)
        variants.append(result)

    if not variants:
        fallback = _build_60_core(pool, fmt, "Midrange", None, power_level, max_price, deck_affinity, meta_scores)
        fallback["archetype_confidence"] = 1.0
        variants.append(fallback)

    primary = variants[0]
    primary["variants"] = variants
    return primary
