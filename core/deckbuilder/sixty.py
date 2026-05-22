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


def _build_60_core(
    pool: list[dict],
    fmt: str,
    archetype: str,
    theme_key: Optional[str],
    power_level: str,
    max_price: Optional[float],
) -> dict:
    """Build a single 60-card deck for one archetype (no variants)."""
    from core.analysis import select_for_curve, deck_synergy_score, score_card, detect_archetypes

    legal_nonland = _apply_power_level_filter(
        [c for c in pool if _is_pool_eligible(c, fmt) and "Land" not in (c.get("type_line") or "")],
        power_level, max_price,
    )

    available: Counter = Counter()
    name_first: dict[str, dict] = {}
    for card in legal_nonland:
        name = (card.get("name_en") or "").lower()
        available[name] += 1
        if name not in name_first:
            name_first[name] = card

    unique_nonland = sorted(
        name_first.values(), key=lambda c: score_card(c, fmt), reverse=True
    )

    target_nonland = 36
    target_lands   = 24

    chosen_names: set[str] = set()
    role_cards, _ = _fill_role_slots(unique_nonland, dict(_60_ROLE_TARGETS), chosen_names)

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

    remaining_target = max(1, target_nonland - len(role_cards))
    theme_cards = select_for_curve(candidates, archetype, remaining_target, fmt="60")

    selected_unique = list(role_cards)
    sel_names: set[str] = {(c.get("name_en") or "").lower() for c in selected_unique}
    for c in theme_cards:
        name = (c.get("name_en") or "").lower()
        if name not in sel_names and len(selected_unique) < target_nonland:
            selected_unique.append(c)
            sel_names.add(name)

    all_unique = list(name_first.values())
    selected_unique = _enforce_diversity(selected_unique, archetype, all_unique, target_nonland, "60")

    deck_cards: list[tuple[dict, int]] = []
    total = 0
    for card in selected_unique:
        if total >= target_nonland:
            break
        name = (card.get("name_en") or "").lower()
        take = min(available[name], _max_copies(card, fmt), target_nonland - total)
        if take > 0:
            deck_cards.append((card, take))
            total += take

    colors_used: set[str] = set()
    for card, _ in deck_cards:
        colors_used |= color_identity(card)
    ci = frozenset(colors_used)

    nonland_for_pips = [c for c, _ in deck_cards]
    land_base = _build_land_base(pool, ci, nonland_for_pips, target_lands, fmt)
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
    value = round(
        sum((c.get("price_eur") or 0) * n for c, n in deck_cards)
        + sum(c.get("price_eur") or 0 for c in nonbasic_lands),
        2,
    )

    return {
        "deck":                   deck_cards,
        "nonbasic_lands":         nonbasic_lands,
        "basics_from_collection": basics_from_collection,
        "basics_missing":         basics_missing,
        "strategy":               (theme_key or "").replace("tribal_", "").title() or archetype,
        "format":                 fmt,
        "collection_count":       collection_count,
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
) -> dict:
    """Build a 60-card deck for the given format.

    Supports standard, modern, legacy, vintage, and pauper.
    Returns primary result with up to 3 archetype variants when strategy is
    auto-detected.
    """
    from core.analysis import detect_archetypes

    legal_nonland = [
        c for c in pool
        if _is_pool_eligible(c, fmt) and "Land" not in (c.get("type_line") or "")
    ]

    if forced_strategy:
        archetype  = _THEME_TO_ARCH.get(forced_strategy, forced_strategy.replace("tribal_", "").title())
        archetypes = [(archetype, 1.0)]
    else:
        archetypes = detect_archetypes(legal_nonland)
        archetype  = archetypes[0][0] if archetypes else "Midrange"

    primary_theme = forced_strategy or _ARCH_TO_THEME.get(archetype)
    primary = _build_60_core(pool, fmt, archetype, primary_theme, power_level, max_price)
    primary["archetype_confidence"] = archetypes[0][1] if archetypes else 1.0

    variants: list[dict] = [primary]
    if not forced_strategy and len(archetypes) > 1:
        for alt_arch, alt_conf in archetypes[1:3]:
            if alt_conf < 0.15:
                break
            alt_theme = _ARCH_TO_THEME.get(alt_arch)
            alt = _build_60_core(pool, fmt, alt_arch, alt_theme, power_level, max_price)
            alt["archetype_confidence"] = round(alt_conf, 3)
            variants.append(alt)

    primary["variants"] = variants
    return primary
