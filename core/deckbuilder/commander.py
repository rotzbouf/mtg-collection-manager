"""Commander deck builder: rank_commanders, build_commander_deck."""
from collections import Counter
from typing import Optional

from ._cards import (
    is_legal, color_identity, get_card_themes,
    is_commander_eligible, _type_group, curve_analysis,
)
from ._roles import tag_card_roles, _fill_role_slots, _commander_synergy_score
from ._mana import _build_land_base
from ._pool import _apply_power_level_filter, _enforce_diversity
from ._constants import _COMMANDER_ROLE_TARGETS


def rank_commanders(pool: list[dict]) -> list[tuple[dict, int]]:
    """Return up to 10 (card, synergy_score) pairs, best first."""
    from core.analysis import score_card

    candidates = [
        c for c in pool
        if is_commander_eligible(c) and is_legal(c, "commander")
    ]
    results = []
    for cmd in candidates:
        ci         = color_identity(cmd)
        cmd_themes = get_card_themes(cmd)
        synergy = sum(
            1 for c in pool
            if color_identity(c).issubset(ci)
            and is_legal(c, "commander")
            and get_card_themes(c) & cmd_themes
        )
        total = synergy * 10 + score_card(cmd, "commander")
        results.append((cmd, synergy, total))
    results.sort(key=lambda x: x[2], reverse=True)
    return [(cmd, syn) for cmd, syn, _ in results[:10]]


_AFFINITY_WEIGHT  = 18.0
_META_WEIGHT      = 0.25  # meta score 0–100; score 100 → +25 pts, score 50 → +12.5 pts
_ANCHOR_MIN_DECKS = 2    # cards in this many user commander decks are pre-selected
_ANCHOR_MAX_SLOTS = 10   # at most this many anchor card slots in commander


def _build_commander_for_archetype(
    commander: dict,
    pool: list[dict],
    archetype: str,
    power_level: str,
    max_price: Optional[float],
    deck_affinity: Optional[dict[str, float]] = None,
    meta_scores: Optional[dict[str, float]] = None,
) -> dict:
    from core.analysis import select_for_curve, deck_synergy_score, score_card, detect_archetypes

    affinity   = deck_affinity or {}
    meta       = meta_scores or {}
    ci         = color_identity(commander)
    cmd_themes = get_card_themes(commander)
    cmd_name   = (commander.get("name_en") or "").lower()

    target_nonland = 63
    target_lands   = 36

    eligible_base = [
        c for c in pool
        if (c.get("name_en") or "").lower() != cmd_name
        and "Land" not in (c.get("type_line") or "")
        and color_identity(c).issubset(ci)
        and is_legal(c, "commander")
    ]
    eligible = _apply_power_level_filter(eligible_base, power_level, max_price)

    def _score(c: dict) -> float:
        name = (c.get("name_en") or "").lower()
        return (
            score_card(c, "commander")
            + len(get_card_themes(c) & cmd_themes) * 8.0
            + _commander_synergy_score(c, commander)
            + affinity.get(name, 0.0) * _AFFINITY_WEIGHT
            + meta.get(name, 0.0) * _META_WEIGHT
        )

    by_name: dict[str, dict] = {}
    for c in eligible:
        name = (c.get("name_en") or "").lower()
        if name not in by_name or _score(c) > _score(by_name[name]):
            by_name[name] = c
    unique_eligible = sorted(by_name.values(), key=_score, reverse=True)

    chosen_names: set[str] = set()
    role_cards, _ = _fill_role_slots(unique_eligible, dict(_COMMANDER_ROLE_TARGETS), chosen_names)

    # Anchor cards: consistently used in the user's existing commander decks.
    # Pre-select them so they always appear in the built deck.
    anchor_cards: list[dict] = []
    if affinity:
        by_name_lookup = {(c.get("name_en") or "").lower(): c for c in unique_eligible}
        for nm, aff_cnt in sorted(affinity.items(), key=lambda x: x[1], reverse=True):
            if len(anchor_cards) >= _ANCHOR_MAX_SLOTS:
                break
            if aff_cnt < _ANCHOR_MIN_DECKS:
                break
            if nm in chosen_names or nm not in by_name_lookup:
                continue
            anchor_cards.append(by_name_lookup[nm])
            chosen_names.add(nm)

    remaining_pool = [c for c in unique_eligible if (c.get("name_en") or "").lower() not in chosen_names]
    remaining_target = max(1, target_nonland - len(role_cards) - len(anchor_cards))
    theme_cards = select_for_curve(remaining_pool[:90], archetype, remaining_target,
                                   fmt="commander", meta_scores=meta_scores)

    deck = list(role_cards) + anchor_cards
    deck_names: set[str] = {(c.get("name_en") or "").lower() for c in deck}
    for c in theme_cards:
        name = (c.get("name_en") or "").lower()
        if name not in deck_names and len(deck) < target_nonland:
            deck.append(c)
            deck_names.add(name)

    deck = _enforce_diversity(deck, archetype, unique_eligible, target_nonland, "commander")

    # If the pool didn't fill all nonland slots, pad with extra basics so the
    # total always reaches 100 (99 + commander).
    actual_nonland    = len(deck)
    nonland_shortfall = max(0, target_nonland - actual_nonland)
    adjusted_lands    = target_lands + nonland_shortfall

    land_base = _build_land_base(pool, ci, deck, adjusted_lands, "commander")
    nonbasic_lands         = land_base["nonbasic_lands"]
    basics_from_collection = land_base["basics_from_collection"]
    basics_missing         = land_base["basics_missing"]

    # Trim to exactly 100 (commander + nonland + lands) when pool is large enough.
    # With padding: 1 + actual_nonland + adjusted_lands = 1 + target_nonland + target_lands = 100
    total_lands = (
        len(nonbasic_lands)
        + len(basics_from_collection)
        + sum(basics_missing.values())
    )
    total = 1 + len(deck) + total_lands
    if total > 100:
        over = total - 100
        deck_scored = sorted(deck, key=_score)
        cut_names = {(c.get("name_en") or "").lower() for c in deck_scored[:over]}
        deck = [c for c in deck if (c.get("name_en") or "").lower() not in cut_names]

    theme_counts: Counter = Counter()
    for c in deck:
        for t in get_card_themes(c) & cmd_themes:
            theme_counts[t] += 1
    top_themes = [t.replace("tribal_", "").title() for t, _ in theme_counts.most_common(5)]

    groups: dict[str, list[dict]] = {}
    for c in deck:
        groups.setdefault(_type_group(c), []).append(c)

    role_summary: Counter = Counter()
    for c in deck:
        for r in tag_card_roles(c):
            role_summary[r] += 1

    synergy = deck_synergy_score(deck[:40])
    all_cards = deck + nonbasic_lands + basics_from_collection
    collection_count     = len(all_cards)
    missing_basics_count = sum(basics_missing.values()) if basics_missing else 0
    # +1 for the commander itself
    total_cards = 1 + collection_count + missing_basics_count   # always 100

    return {
        "commander":              commander,
        "deck":                   deck,
        "nonbasic_lands":         nonbasic_lands,
        "basics_from_collection": basics_from_collection,
        "basics_missing":         basics_missing,
        "padding_basics":         nonland_shortfall,
        "groups":                 groups,
        "themes":                 top_themes,
        "collection_count":       collection_count,
        "total_cards":            total_cards,
        "value_eur":              round(sum(c.get("price_eur") or 0 for c in all_cards), 2),
        "curve":                  curve_analysis([(c, 1) for c in deck]),
        "archetype":              archetype,
        "archetypes":             detect_archetypes(deck)[:3],
        "synergy_score":          synergy,
        "role_summary":           dict(role_summary),
        "power_level":            power_level,
    }


def build_commander_deck(
    commander: dict,
    pool: list[dict],
    power_level: str = "focused",
    max_price: Optional[float] = None,
    deck_affinity: Optional[dict[str, float]] = None,
    meta_scores: Optional[dict[str, float]] = None,
) -> dict:
    """Build a commander deck; returns primary result with up to 3 archetype variants.

    meta_scores: optional {card_name_lower: 0–100} from the competitive meta
                 DB; higher-scoring cards get a modest scoring bonus.
    """
    from core.analysis import detect_archetypes, meta_preferred_archetype

    ci = color_identity(commander)
    sample = [
        c for c in pool
        if "Land" not in (c.get("type_line") or "")
        and color_identity(c).issubset(ci)
        and is_legal(c, "commander")
    ]
    archetypes = detect_archetypes([commander] + sample)

    # When meta data is available and the collection doesn't strongly signal an
    # archetype, nudge toward what the competitive meta favours.
    if meta_scores and (not archetypes or archetypes[0][1] < 0.70):
        meta_arch = meta_preferred_archetype(meta_scores, sample)
        if meta_arch and (not archetypes or meta_arch != archetypes[0][0]):
            existing = [(a, c) for a, c in archetypes if a != meta_arch]
            archetypes = [(meta_arch, 1.0)] + existing[:2]

    variants: list[dict] = []
    for arch, conf in archetypes[:3]:
        if conf < 0.15 and variants:
            break
        result = _build_commander_for_archetype(
            commander, pool, arch, power_level, max_price, deck_affinity, meta_scores
        )
        result["archetype_confidence"] = round(conf, 3)
        variants.append(result)

    if not variants:
        result = _build_commander_for_archetype(
            commander, pool, "default", power_level, max_price, deck_affinity, meta_scores
        )
        result["archetype_confidence"] = 1.0
        variants = [result]

    primary = variants[0]
    primary["variants"]  = variants
    primary["archetypes"] = archetypes[:3]
    return primary
