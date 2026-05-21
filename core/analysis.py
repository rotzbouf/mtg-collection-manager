"""Card scoring, synergy engine, archetype detection, and mana-curve optimization."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ── Card power scoring ─────────────────────────────────────────────────────────

KEYWORD_WEIGHTS: dict[str, float] = {
    "flying":         2.0,
    "trample":        1.5,
    "menace":         1.5,
    "shadow":         1.5,
    "unblockable":    2.5,
    "double strike":  3.0,
    "first strike":   1.5,
    "deathtouch":     2.5,
    "lifelink":       1.5,
    "vigilance":      1.0,
    "haste":          2.0,
    "hexproof":       3.0,
    "indestructible": 3.5,
    "protection":     2.5,
    "shroud":         2.0,
    "ward":           2.0,
    "flash":          1.5,
    "prowess":        1.5,
    "cascade":        4.0,
    "annihilator":    4.5,
    "dredge":         2.0,
    "storm":          4.0,
    "affinity":       2.0,
    "convoke":        1.0,
    "emerge":         1.0,
}

_EFFECT_WEIGHTS: list[tuple[str, float]] = [
    ("draw a card",                     2.5),
    ("draw two cards",                  4.0),
    ("draw three cards",                5.0),
    ("draw x cards",                    4.5),
    ("draw cards equal",                4.0),
    ("scry",                            1.0),
    ("surveil",                         1.5),
    ("search your library for a basic land", 3.0),
    ("search your library for a land",       3.5),
    ("search your library for a card",       4.5),
    ("search your library for any",          5.0),
    ("add {",                           2.0),
    ("add two mana",                    2.5),
    ("add three mana",                  3.0),
    ("mana of any color",               2.5),
    ("destroy target",                  3.0),
    ("exile target",                    3.5),
    ("counter target spell",            4.0),
    ("return target",                   2.0),
    ("damage to any target",            2.5),   # matches "deals 3 damage to any target"
    ("damage to each",                  2.5),
    ("destroy all",                     4.5),
    ("exile all",                       5.0),
    ("each opponent",                   2.5),
    ("extra turn",                      5.0),
    ("untap all",                       4.0),
    ("flashback",                       1.5),
    ("escape",                          1.5),
    ("unearth",                         1.0),
    ("create a",                        1.0),
    ("create two",                      2.0),
    ("create three",                    2.5),
    ("create x",                        2.5),
    ("proliferate",                     2.5),
    ("whenever you draw",               3.0),
]

_RARITY_BONUS: dict[str, float] = {
    "mythic":   8.0,
    "rare":     5.0,
    "uncommon": 2.0,
    "common":   0.0,
}


def score_card(card: dict, fmt: str = "commander") -> float:
    """Return a 0–100 power/value heuristic for *card* in *fmt*.

    Most cards land in 10–40; exceptional cards reach 60–80.
    This is a relative ranking tool — not an absolute power rating.
    """
    score = 0.0
    oracle = (card.get("oracle_text") or "").lower()

    keywords_raw = card.get("keywords") or []
    if isinstance(keywords_raw, str):
        try:
            keywords_raw = json.loads(keywords_raw)
        except Exception:
            keywords_raw = []
    keywords_lower = {k.lower() for k in keywords_raw}

    for kw, w in KEYWORD_WEIGHTS.items():
        if kw in keywords_lower or kw in oracle:
            score += w

    for pattern, w in _EFFECT_WEIGHTS:
        if pattern in oracle:
            score += w

    tl = card.get("type_line") or ""
    if "Creature" in tl:
        try:
            pw = int(card.get("power") or 0)
            tg = int(card.get("toughness") or 0)
            cmc = float(card.get("cmc") or 1)
            if cmc > 0:
                score += min((pw + tg) / cmc * 1.5, 6.0)
        except (ValueError, ZeroDivisionError):
            pass

    score += _RARITY_BONUS.get(card.get("rarity") or "", 0.0)

    if fmt == "commander":
        if "each opponent" in oracle or "all players" in oracle:
            score += 3.0

    return round(min(score, 100.0), 2)


# ── Synergy engine ─────────────────────────────────────────────────────────────

# (pattern_in_A, pattern_in_B, score) — checked symmetrically
_SYNERGY_RULES: list[tuple[str, str, float]] = [
    # Sacrifice
    ("sacrifice a ",             "whenever a creature dies",           4.0),
    ("sacrifice a creature",     "when a creature you control dies",   4.0),
    ("sacrifice a ",             "whenever you sacrifice",             4.0),
    ("sacrifice a ",             "dies,",                              3.0),
    # Tokens
    ("create a",                 "each creature you control gets",     3.5),
    ("create a",                 "for each creature you control",      3.5),
    ("create a",                 "whenever a creature enters",         3.0),
    ("create a",                 "convoke",                            2.5),
    # Counters
    ("+1/+1 counter",            "proliferate",                        4.5),
    ("+1/+1 counter",            "adapt",                              3.5),
    ("put a counter on",         "+1/+1 counters",                     3.5),
    # Graveyard
    ("graveyard",                "flashback",                          3.0),
    ("graveyard",                "unearth",                            3.0),
    ("mill",                     "graveyard",                          3.5),
    ("discard",                  "madness",                            4.0),
    ("discard",                  "graveyard",                          3.0),
    # Card draw
    ("draw a card",              "whenever you draw",                  4.5),
    ("draw a card",              "storm",                              4.0),
    # Enchantments
    ("enchantment",              "constellation",                      4.5),
    ("aura",                     "enchanted creature gets",            3.5),
    # Equipment
    ("equip",                    "equipped creature gets",             4.0),
    ("equipment",                "whenever equipped creature",         4.0),
    # Spellcasting
    ("when you cast",            "magecraft",                          4.5),
    ("instant or sorcery",       "prowess",                            3.5),
    ("instant or sorcery",       "whenever you cast",                  4.0),
    # Lifegain
    ("gain life",                "whenever you gain life",             4.5),
    ("lifelink",                 "whenever you gain life",             4.0),
    # Landfall
    ("land enters",              "landfall",                           4.5),
    ("basic land",               "landfall",                           3.5),
    # Ramp + bombs
    ("add {",                    "cmc",                                1.5),
    ("search your library for a land", "landfall",                    3.0),
    # Tribal (same creature type in type line)
]

_TRIBAL_TYPES = [
    "elf", "goblin", "zombie", "vampire", "dragon", "merfolk", "human",
    "soldier", "wizard", "warrior", "knight", "cleric", "rogue", "angel",
    "demon", "dinosaur", "pirate", "cat", "sliver", "elemental", "spirit",
    "faerie", "snake", "rat", "werewolf", "shaman", "beast",
]


def _shared_tribes(card_a: dict, card_b: dict) -> int:
    tl_a = (card_a.get("type_line") or "").lower()
    tl_b = (card_b.get("type_line") or "").lower()
    count = 0
    for t in _TRIBAL_TYPES:
        if t in tl_a and t in tl_b:
            count += 1
    return count


def pairwise_synergy(card_a: dict, card_b: dict) -> float:
    """Return a 0–10 synergy score for two cards based on mechanical interaction."""
    from core.deckbuilder import get_card_themes

    a = (card_a.get("oracle_text") or "").lower()
    b = (card_b.get("oracle_text") or "").lower()
    tl_a = (card_a.get("type_line") or "")
    tl_b = (card_b.get("type_line") or "")

    score = 0.0
    for pat_a, pat_b, w in _SYNERGY_RULES:
        if (pat_a in a and pat_b in b) or (pat_a in b and pat_b in a):
            score += w

    # Type-line based: Instant/Sorcery → prowess / magecraft / "when you cast" triggers
    _spell_types = ("Instant", "Sorcery")
    _spell_triggers = ("when you cast", "instant or sorcery", "magecraft", "prowess")
    for tl_check, oracle_check in ((tl_a, b), (tl_b, a)):
        if any(t in tl_check for t in _spell_types):
            if any(p in oracle_check for p in _spell_triggers):
                score += 3.5

    # Shared themes from deckbuilder
    themes_a = get_card_themes(card_a)
    themes_b = get_card_themes(card_b)
    score += len(themes_a & themes_b) * 0.6

    # Tribal bonus
    score += _shared_tribes(card_a, card_b) * 1.5

    return round(min(score, 10.0), 2)


def deck_synergy_score(cards: list[dict]) -> float:
    """Mean pairwise synergy across all card pairs (0–10).

    Capped at 500 pairs for performance on large pools.
    """
    if len(cards) < 2:
        return 0.0
    total = 0.0
    count = 0
    limit = 500
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            total += pairwise_synergy(cards[i], cards[j])
            count += 1
            if count >= limit:
                break
        if count >= limit:
            break
    return round(total / count, 2) if count else 0.0


def top_synergy_pairs(cards: list[dict], n: int = 5) -> list[tuple[dict, dict, float]]:
    """Return the top-n (card_a, card_b, score) pairs by synergy."""
    pairs: list[tuple[dict, dict, float]] = []
    for i in range(len(cards)):
        for j in range(i + 1, len(cards)):
            s = pairwise_synergy(cards[i], cards[j])
            if s > 0:
                pairs.append((cards[i], cards[j], s))
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs[:n]


# ── Archetype detection ────────────────────────────────────────────────────────

_ARCHETYPE_SIGNALS: dict[str, list[tuple[str, float, str]]] = {
    "Aggro": [
        ("haste",                   3.0, "oracle"),
        ("first strike",            2.0, "oracle"),
        ("double strike",           3.0, "oracle"),
        ("menace",                  2.0, "oracle"),
        ("trample",                 1.5, "oracle"),
        ("attacks each",            2.5, "oracle"),
        ("deals damage",            2.0, "oracle"),
        ("burn",                    1.5, "oracle"),
    ],
    "Control": [
        ("counter target spell",    5.0, "oracle"),
        ("counter target",          4.0, "oracle"),
        ("destroy target",          3.0, "oracle"),
        ("exile target",            3.5, "oracle"),
        ("return target",           2.0, "oracle"),
        ("draw a card",             2.0, "oracle"),
        ("draw two cards",          3.0, "oracle"),
        ("destroy all",             4.0, "oracle"),
        ("exile all",               4.5, "oracle"),
        ("each creature",           2.5, "oracle"),
        ("Instant",                 1.0, "type"),
    ],
    "Midrange": [
        ("enters the battlefield",  2.0, "oracle"),
        ("when ~ enters",           2.0, "oracle"),
        ("lifelink",                1.5, "oracle"),
        ("vigilance",               1.5, "oracle"),
        ("dies,",                   2.0, "oracle"),
        ("Creature",                0.5, "type"),
    ],
    "Ramp": [
        ("search your library for a basic land", 5.0, "oracle"),
        ("search your library for a land",       5.0, "oracle"),
        ("add {",                   3.0, "oracle"),
        ("add two mana",            3.5, "oracle"),
        ("add three mana",          4.0, "oracle"),
        ("mana of any color",       3.0, "oracle"),
        ("additional land",         4.5, "oracle"),
        ("land card",               2.0, "oracle"),
    ],
    "Tokens": [
        ("create a",                4.0, "oracle"),
        ("create two",              5.0, "oracle"),
        ("create x",                5.0, "oracle"),
        ("populate",                4.5, "oracle"),
        ("convoke",                 3.0, "oracle"),
        ("for each token",          4.0, "oracle"),
        ("creatures you control get +", 3.0, "oracle"),
        ("each token you control",  4.0, "oracle"),
    ],
    "Graveyard": [
        ("graveyard",               1.5, "oracle"),
        ("flashback",               4.0, "oracle"),
        ("unearth",                 3.5, "oracle"),
        ("dredge",                  4.5, "oracle"),
        ("escape",                  3.5, "oracle"),
        ("embalm",                  3.5, "oracle"),
        ("delve",                   3.5, "oracle"),
        ("mill",                    2.5, "oracle"),
        ("return target creature card from", 3.0, "oracle"),
        ("discard",                 1.5, "oracle"),
    ],
    "Combo": [
        ("storm",                   5.0, "oracle"),
        ("cascade",                 4.0, "oracle"),
        ("copy target",             4.0, "oracle"),
        ("extra turn",              5.0, "oracle"),
        ("untap all",               4.5, "oracle"),
        ("whenever you draw",       3.5, "oracle"),
        ("search your library for a card", 4.0, "oracle"),
        ("search your library for any",    5.0, "oracle"),
    ],
    "Voltron": [
        ("equip",                   3.0, "oracle"),
        ("Equipment",               3.0, "type"),
        ("Aura",                    2.5, "type"),
        ("bestow",                  2.5, "oracle"),
        ("double strike",           3.0, "oracle"),
        ("hexproof",                3.0, "oracle"),
        ("indestructible",          3.0, "oracle"),
        ("+2/+2",                   2.0, "oracle"),
        ("+3/+3",                   2.5, "oracle"),
    ],
    "Spellslinger": [
        ("when you cast an instant or sorcery", 5.0, "oracle"),
        ("when you cast",           3.0, "oracle"),
        ("magecraft",               5.0, "oracle"),
        ("prowess",                 3.5, "oracle"),
        ("each instant and sorcery", 4.0, "oracle"),
        ("Instant",                 1.0, "type"),
        ("Sorcery",                 1.0, "type"),
    ],
}


def detect_archetypes(pool: list[dict]) -> list[tuple[str, float]]:
    """Return (archetype_name, normalized_confidence) sorted descending.

    Confidence is normalized: the strongest archetype = 1.0.
    Archetypes below 8% relative score are excluded.
    """
    raw: dict[str, float] = {arch: 0.0 for arch in _ARCHETYPE_SIGNALS}

    for card in pool:
        oracle = (card.get("oracle_text") or "").lower()
        tl     = (card.get("type_line")   or "")

        for arch, signals in _ARCHETYPE_SIGNALS.items():
            for pattern, weight, field in signals:
                text = oracle if field == "oracle" else tl
                if pattern.lower() in text:
                    raw[arch] += weight

    n = max(len(pool), 1)
    for arch in raw:
        raw[arch] /= n

    peak = max(raw.values(), default=0.0)
    if peak == 0:
        return []

    results = [
        (arch, round(raw[arch] / peak, 3))
        for arch in raw
        if raw[arch] / peak >= 0.08
    ]
    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ── Ideal mana curves ──────────────────────────────────────────────────────────

# Keys: CMC bucket (0–6+). Values: fraction of nonland slots.
_IDEAL_60: dict[str, dict[int, float]] = {
    "Aggro":       {0: 0.05, 1: 0.25, 2: 0.28, 3: 0.22, 4: 0.10, 5: 0.06, 6: 0.04},
    "Control":     {0: 0.03, 1: 0.05, 2: 0.15, 3: 0.20, 4: 0.20, 5: 0.20, 6: 0.17},
    "Midrange":    {0: 0.03, 1: 0.08, 2: 0.22, 3: 0.28, 4: 0.22, 5: 0.12, 6: 0.05},
    "Ramp":        {0: 0.02, 1: 0.05, 2: 0.20, 3: 0.15, 4: 0.15, 5: 0.13, 6: 0.30},
    "Tokens":      {0: 0.02, 1: 0.10, 2: 0.25, 3: 0.30, 4: 0.20, 5: 0.10, 6: 0.03},
    "Graveyard":   {0: 0.02, 1: 0.10, 2: 0.25, 3: 0.25, 4: 0.20, 5: 0.12, 6: 0.06},
    "Combo":       {0: 0.03, 1: 0.15, 2: 0.30, 3: 0.25, 4: 0.15, 5: 0.08, 6: 0.04},
    "Spellslinger":{0: 0.02, 1: 0.10, 2: 0.30, 3: 0.30, 4: 0.15, 5: 0.10, 6: 0.03},
    "Voltron":     {0: 0.02, 1: 0.05, 2: 0.20, 3: 0.25, 4: 0.25, 5: 0.15, 6: 0.08},
    "default":     {0: 0.02, 1: 0.10, 2: 0.25, 3: 0.28, 4: 0.20, 5: 0.12, 6: 0.03},
}

_IDEAL_100: dict[str, dict[int, float]] = {
    "Aggro":       {0: 0.04, 1: 0.20, 2: 0.28, 3: 0.24, 4: 0.12, 5: 0.08, 6: 0.04},
    "Control":     {0: 0.02, 1: 0.05, 2: 0.12, 3: 0.18, 4: 0.20, 5: 0.22, 6: 0.21},
    "Midrange":    {0: 0.02, 1: 0.08, 2: 0.20, 3: 0.28, 4: 0.22, 5: 0.14, 6: 0.06},
    "Ramp":        {0: 0.02, 1: 0.05, 2: 0.18, 3: 0.15, 4: 0.12, 5: 0.15, 6: 0.33},
    "Tokens":      {0: 0.02, 1: 0.08, 2: 0.22, 3: 0.30, 4: 0.22, 5: 0.12, 6: 0.04},
    "Graveyard":   {0: 0.02, 1: 0.10, 2: 0.22, 3: 0.25, 4: 0.20, 5: 0.13, 6: 0.08},
    "Combo":       {0: 0.02, 1: 0.12, 2: 0.28, 3: 0.28, 4: 0.18, 5: 0.08, 6: 0.04},
    "Spellslinger":{0: 0.02, 1: 0.12, 2: 0.28, 3: 0.28, 4: 0.18, 5: 0.08, 6: 0.04},
    "Voltron":     {0: 0.02, 1: 0.05, 2: 0.18, 3: 0.25, 4: 0.25, 5: 0.17, 6: 0.08},
    "default":     {0: 0.02, 1: 0.08, 2: 0.20, 3: 0.28, 4: 0.22, 5: 0.14, 6: 0.06},
}


def _ideal_table(fmt: str) -> dict[str, dict[int, float]]:
    return _IDEAL_100 if fmt == "commander" else _IDEAL_60


def ideal_curve(archetype: str, fmt: str = "commander") -> dict[int, float]:
    """Return the ideal CMC fraction distribution for an archetype."""
    table = _ideal_table(fmt)
    return table.get(archetype, table["default"])


def curve_fit_score(actual_curve: dict[int, int], archetype: str, fmt: str = "commander") -> float:
    """Cosine similarity (0–1) between actual CMC distribution and the archetype ideal."""
    ideal = ideal_curve(archetype, fmt)
    total = sum(actual_curve.values())
    if total == 0:
        return 0.0
    actual_fracs = {k: v / total for k, v in actual_curve.items()}
    dot = sum(actual_fracs.get(b, 0.0) * ideal.get(b, 0.0) for b in range(7))
    mag_a = math.sqrt(sum(v * v for v in actual_fracs.values()))
    mag_i = math.sqrt(sum(v * v for v in ideal.values()))
    if mag_a == 0 or mag_i == 0:
        return 0.0
    return round(dot / (mag_a * mag_i), 3)


def card_deck_fit_score(
    card: dict,
    deck: list[dict],
    archetype: str,
    commander: dict | None = None,
    fmt: str = "commander",
    role_gaps: dict[str, int] | None = None,
    _deck_curve: dict[int, int] | None = None,
) -> float:
    """Composite fit score for a card in the context of a specific deck (0–150).

    Combines base power, archetype theme alignment, commander synergy,
    role-gap fill bonus, and mana-curve position bonus.

    Pass _deck_curve (pre-computed) to avoid re-computing per card in tight loops.
    """
    from core.deckbuilder import (
        get_card_themes, tag_card_roles,
        _commander_synergy_score, _ARCH_TO_THEME,
    )

    base = score_card(card, fmt)

    themes = get_card_themes(card)
    arch_theme = _ARCH_TO_THEME.get(archetype, "")
    if arch_theme and arch_theme in themes:
        base += 15.0
    base += len(themes) * 1.2

    if commander is not None:
        base += _commander_synergy_score(card, commander) * 1.5

    if role_gaps:
        for r in tag_card_roles(card):
            gap = role_gaps.get(r, 0)
            if gap > 0:
                base += min(gap * 2.5, 10.0)
                break

    # Curve-position bonus: reward cards filling under-represented CMC buckets
    if _deck_curve is None:
        _deck_curve = {}
        for c in deck:
            b = min(int(c.get("cmc") or 0), 6)
            _deck_curve[b] = _deck_curve.get(b, 0) + 1

    ideal = ideal_curve(archetype, fmt)
    total_deck = max(len(deck), 1)
    cmc_bucket = min(int(card.get("cmc") or 0), 6)
    actual_frac = _deck_curve.get(cmc_bucket, 0) / total_deck
    ideal_frac = ideal.get(cmc_bucket, 0.0)
    if actual_frac < ideal_frac:
        base += (ideal_frac - actual_frac) * 25.0

    return round(min(base, 150.0), 2)


def select_for_curve(
    candidates: list[dict],
    archetype: str,
    target_count: int,
    fmt: str = "commander",
) -> list[dict]:
    """Pick *target_count* cards from *candidates* to best fit the archetype's ideal curve.

    Within each CMC bucket, cards are sorted by score_card() descending.
    Surplus capacity from empty buckets spills into adjacent filled ones.
    """
    ideal = ideal_curve(archetype, fmt)

    # Target per bucket (integer allocation from fractions)
    targets: dict[int, int] = {}
    allocated = 0
    for b in range(7):
        n = round(ideal.get(b, 0) * target_count)
        targets[b] = n
        allocated += n
    # Fix rounding drift
    diff = target_count - allocated
    if diff != 0:
        peak_bucket = max(ideal, key=lambda k: ideal[k])
        targets[peak_bucket] += diff

    # Group candidates by CMC bucket, ranked by card score
    by_bucket: dict[int, list[dict]] = defaultdict(list)
    for card in candidates:
        b = min(int(card.get("cmc") or 0), 6)
        by_bucket[b].append(card)
    for b in by_bucket:
        by_bucket[b].sort(key=lambda c: score_card(c, fmt), reverse=True)

    selected: list[dict] = []
    overflow: list[dict] = []
    for b in range(7):
        pool_b = by_bucket.get(b, [])
        take = min(targets.get(b, 0), len(pool_b))
        selected.extend(pool_b[:take])
        overflow.extend(pool_b[take:])

    remaining = target_count - len(selected)
    if remaining > 0:
        overflow.sort(key=lambda c: score_card(c, fmt), reverse=True)
        selected.extend(overflow[:remaining])

    return selected[:target_count]
