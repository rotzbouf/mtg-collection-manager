"""
Deck building engine.
Formats: commander, standard, modern, legacy, vintage, pauper, timeless
"""

import json
from collections import Counter
from typing import Optional

# ── Theme detection ────────────────────────────────────────────────────────────

THEMES: dict[str, list[str]] = {
    "tokens":       ["create", " token", "populate", "convoke"],
    "counters":     ["+1/+1", "proliferate", "bolster", "adapt", "modular",
                     "evolve", "undying", "persist", "put a counter"],
    "graveyard":    ["graveyard", "flashback", "unearth", "delve", "dredge",
                     "escape", "embalm", "eternalize"],
    "draw":         ["draw a card", "draw two", "draw three", "draw x", "scry"],
    "discard":      ["discard", "madness", "cycling"],
    "sacrifice":    ["sacrifice a ", "sacrifice another", "exploit", "devour", "emerge"],
    "lifegain":     ["lifelink", "gain life", "you gain", "extort"],
    "equipment":    ["equipment", "equip "],
    "enchantress":  ["enchantment", "constellation", " aura", "bestow"],
    "artifacts":    ["artifact", "affinity", "improvise", "metalcraft"],
    "burn":         ["deals damage", "damage to any target", "damage to each"],
    "control":      ["counter target spell", "return target", "exile target", "destroy target"],
    "ramp":         ["search your library for a basic land",
                     "search your library for a land", "add {", "mana of any color"],
    "spellslinger": ["when you cast", "magecraft", "prowess", "each instant and sorcery"],
    "voltron":      ["double strike", "trample", "+2/+2", "+3/+3",
                     "hexproof", "indestructible"],
}

_TRIBAL_TYPES = [
    "Elf", "Goblin", "Zombie", "Vampire", "Dragon", "Merfolk", "Human",
    "Soldier", "Wizard", "Warrior", "Knight", "Cleric", "Rogue", "Druid",
    "Angel", "Demon", "Dinosaur", "Pirate", "Cat", "Dog", "Sliver",
    "Beast", "Bird", "Spirit", "Elemental", "Giant", "Faerie", "Snake",
    "Rat", "Horror", "Werewolf", "Shaman",
]

COLOR_BASICS = {
    "W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"
}

# ── Role detection constants ───────────────────────────────────────────────────

_ROLE_PATTERNS: dict[str, list[str]] = {
    "ramp": [
        "search your library for a basic land",
        "search your library for a land",
        "land card from your library",
        "additional land",
        "add two mana", "add three mana",
        "mana of any color",
    ],
    "removal": [
        "destroy target", "exile target",
        "destroy all creatures", "exile all creatures",
        "counter target spell", "counter target",
        "damage to any target",
        "-x/-x", "−x/−x",
        "return target", "put on the bottom",
    ],
    "draw": [
        "draw a card", "draw two cards", "draw three cards",
        "draw x cards", "draw cards equal",
        "whenever you draw", "scry ", "surveil ",
    ],
    "board_wipe": [
        "destroy all creatures", "exile all creatures",
        "deals damage to each creature",
        "each creature gets -",
        "all creatures get -",
    ],
    "wincon": [
        "you win the game",
        "extra turn",
        "annihilator",
        "infect",
        "poison counter",
    ],
}

# Guaranteed role-slot targets per format
_COMMANDER_ROLE_TARGETS: dict[str, int] = {
    "ramp":    12,
    "removal": 10,
    "draw":    10,
    "wincon":   4,
}

_60_ROLE_TARGETS: dict[str, int] = {
    "ramp":    4,
    "removal": 6,
    "draw":    4,
}

# Minimum card-type counts per archetype
_DIVERSITY_MINIMUMS_CMD: dict[str, dict[str, int]] = {
    "Aggro":       {"Creatures": 20},
    "Control":     {"Instants": 8,  "Creatures": 6},
    "Midrange":    {"Creatures": 16},
    "Ramp":        {"Creatures": 8,  "Sorceries": 6},
    "Tokens":      {"Creatures": 10, "Sorceries": 6},
    "Graveyard":   {"Creatures": 14},
    "Combo":       {"Instants": 6,   "Sorceries": 6},
    "Spellslinger":{"Instants": 10,  "Sorceries": 8,  "Creatures": 8},
    "Voltron":     {"Creatures": 8,  "Artifacts": 8},
    "default":     {"Creatures": 12},
}

_DIVERSITY_MINIMUMS_60: dict[str, dict[str, int]] = {
    "Aggro":       {"Creatures": 16, "Instants": 4},
    "Control":     {"Instants": 10,  "Creatures": 4},
    "Midrange":    {"Creatures": 12},
    "Ramp":        {"Creatures": 6,  "Sorceries": 4},
    "Tokens":      {"Creatures": 8,  "Sorceries": 4},
    "Graveyard":   {"Creatures": 10},
    "Combo":       {"Instants": 6,   "Sorceries": 6},
    "Spellslinger":{"Instants": 8,   "Sorceries": 6,  "Creatures": 4},
    "Voltron":     {"Creatures": 4,  "Artifacts": 6},
    "default":     {"Creatures": 8},
}

_THEME_TO_ARCH: dict[str, str] = {
    "tokens": "Tokens", "graveyard": "Graveyard", "control": "Control",
    "ramp": "Ramp", "spellslinger": "Spellslinger", "voltron": "Voltron",
    "burn": "Aggro", "discard": "Graveyard", "sacrifice": "Midrange",
    "counters": "Midrange", "lifegain": "Midrange", "draw": "Control",
    "equipment": "Voltron", "enchantress": "Control", "artifacts": "Midrange",
}

_ARCH_TO_THEME: dict[str, str] = {
    "Aggro": "burn", "Control": "control", "Midrange": "counters",
    "Ramp": "ramp", "Tokens": "tokens", "Graveyard": "graveyard",
    "Combo": "draw", "Spellslinger": "spellslinger", "Voltron": "voltron",
}

# ── Core helpers ───────────────────────────────────────────────────────────────

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

    Identical to is_legal for every format except vintage, where restricted
    cards (max 1 copy) are still playable.
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


# ── Mana pip analysis ──────────────────────────────────────────────────────────

def _count_mana_pips(cards: list[dict]) -> dict[str, float]:
    """Count coloured mana symbols (including hybrid) across all cards' mana costs."""
    pips: dict[str, float] = {c: 0.0 for c in "WUBRG"}
    for card in cards:
        mana = card.get("mana_cost") or ""
        for color in "WUBRG":
            pips[color] += mana.count(f"{{{color}}}")
            for other in "WUBRG":
                if other != color:
                    pips[color] += mana.count(f"{{{color}/{other}}}") * 0.5
                    pips[color] += mana.count(f"{{{other}/{color}}}") * 0.5
            pips[color] += mana.count(f"{{{color}/P}}") * 0.5
    return pips


def _pip_weighted_land_split(
    pip_counts: dict[str, float],
    colors: frozenset,
    total_basics: int,
) -> dict[str, int]:
    """Distribute basic land slots proportionally to pip counts."""
    if not colors or total_basics <= 0:
        return {}
    relevant = {c: max(pip_counts.get(c, 1.0), 1.0) for c in colors}
    total_pips = sum(relevant.values())
    split: dict[str, int] = {}
    remaining = total_basics
    by_pips = sorted(relevant.items(), key=lambda x: x[1], reverse=True)
    for i, (color, pips) in enumerate(by_pips):
        if i == len(by_pips) - 1:
            split[color] = max(1, remaining)
        else:
            count = max(1, round(pips / total_pips * total_basics))
            split[color] = count
            remaining -= count
    return split


# ── Role classification ────────────────────────────────────────────────────────

def _is_mana_rock(card: dict) -> bool:
    tl     = card.get("type_line")   or ""
    oracle = (card.get("oracle_text") or "").lower()
    return (
        "Artifact" in tl
        and "Creature" not in tl
        and "Land" not in tl
        and (
            "add {" in oracle
            or "add two mana" in oracle
            or "add three mana" in oracle
            or "add one mana" in oracle
            or "mana of any color" in oracle
        )
    )


def tag_card_roles(card: dict) -> set[str]:
    """Return the functional roles of a card: ramp, removal, draw, board_wipe, wincon."""
    oracle = (card.get("oracle_text") or "").lower()
    roles: set[str] = set()
    for role, patterns in _ROLE_PATTERNS.items():
        if any(p in oracle for p in patterns):
            roles.add(role)
    if _is_mana_rock(card):
        roles.add("ramp")
    if "board_wipe" in roles:
        roles.add("removal")
    return roles


# ── Commander-specific synergy boost ──────────────────────────────────────────

def _commander_synergy_score(card: dict, commander: dict) -> float:
    """Extra score for cards that synergize specifically with this commander."""
    cmd_tl     = commander.get("type_line")   or ""
    cmd_oracle = (commander.get("oracle_text") or "").lower()
    cmd_name   = (commander.get("name_en")    or "").lower()

    card_oracle = (card.get("oracle_text") or "").lower()
    card_tl     = (card.get("type_line")   or "").lower()

    score = 0.0

    # Shared creature subtypes (tribal)
    cmd_subtypes: set[str] = set()
    if " — " in cmd_tl:
        sub_part = cmd_tl.split(" — ", 1)[1]
        cmd_subtypes = {s.strip().lower() for s in sub_part.split() if len(s.strip()) > 2}
    elif " - " in cmd_tl:
        sub_part = cmd_tl.split(" - ", 1)[1]
        cmd_subtypes = {s.strip().lower() for s in sub_part.split() if len(s.strip()) > 2}

    for subtype in cmd_subtypes:
        if subtype in card_oracle or subtype in card_tl:
            score += 2.0

    # Card references the commander's name
    if cmd_name and len(cmd_name) > 4 and cmd_name in card_oracle:
        score += 5.0

    # Commander trigger → match enablers in candidate cards
    trigger_map: list[tuple[str, list[str]]] = [
        ("when you cast an instant or sorcery", ["instant", "sorcery"]),
        ("whenever you cast",                  ["instant", "sorcery"]),
        ("whenever a creature dies",           ["sacrifice", "destroy"]),
        ("whenever you gain life",             ["lifelink", "gain life"]),
        ("landfall",                           ["basic land", "land enters", "search your library for a land"]),
        ("whenever a token",                   ["create a", "token"]),
        ("whenever you draw",                  ["draw a card", "scry", "surveil"]),
        ("+1/+1 counter",                      ["proliferate", "put a counter"]),
    ]
    for cmd_trigger, enablers in trigger_map:
        if cmd_trigger in cmd_oracle:
            for enabler in enablers:
                if enabler in card_oracle:
                    score += 1.5

    return min(score, 15.0)


# ── Power-level / budget filter ────────────────────────────────────────────────

def _apply_power_level_filter(
    pool: list[dict],
    power_level: str,
    max_price: Optional[float],
) -> list[dict]:
    result = []
    for card in pool:
        price = card.get("price_eur") or 0.0
        if max_price is not None and price > max_price:
            continue
        if power_level == "casual" and price > 5.0:
            continue
        result.append(card)
    return result


# ── Role-slot filling ──────────────────────────────────────────────────────────

def _fill_role_slots(
    pool_sorted: list[dict],
    role_targets: dict[str, int],
    chosen_names: set[str],
) -> tuple[list[dict], dict[str, int]]:
    """Pick highest-scored cards to fill role quotas."""
    chosen: list[dict] = []
    remaining = {r: n for r, n in role_targets.items() if n > 0}
    priority = ("ramp", "removal", "draw", "wincon", "board_wipe")
    for card in pool_sorted:
        if not remaining:
            break
        name = (card.get("name_en") or "").lower()
        if name in chosen_names:
            continue
        roles = tag_card_roles(card)
        for role in priority:
            if role in roles and role in remaining:
                chosen.append(card)
                chosen_names.add(name)
                remaining[role] -= 1
                if remaining[role] <= 0:
                    del remaining[role]
                break
    return chosen, remaining


# ── Diversity enforcement ──────────────────────────────────────────────────────

def _enforce_diversity(
    deck: list[dict],
    archetype: str,
    pool: list[dict],
    target_count: int,
    fmt: str,
) -> list[dict]:
    """Ensure minimum card-type counts; replace lowest-scoring non-role cards if needed."""
    from core.analysis import score_card

    minimums = (
        _DIVERSITY_MINIMUMS_CMD if fmt == "commander" else _DIVERSITY_MINIMUMS_60
    ).get(archetype, {"Creatures": 12 if fmt == "commander" else 8})

    if not minimums:
        return deck

    deck_names = {(c.get("name_en") or "").lower() for c in deck}
    type_counts: Counter = Counter(_type_group(c) for c in deck)
    sfmt = "commander" if fmt == "commander" else fmt

    additions: list[dict] = []
    for type_name, minimum in minimums.items():
        needed = minimum - type_counts.get(type_name, 0)
        if needed <= 0:
            continue
        candidates = sorted(
            [c for c in pool
             if _type_group(c) == type_name
             and (c.get("name_en") or "").lower() not in deck_names],
            key=lambda c: score_card(c, sfmt),
            reverse=True,
        )
        for c in candidates[:needed]:
            additions.append(c)
            deck_names.add((c.get("name_en") or "").lower())

    if not additions:
        return deck

    combined = deck + additions
    if len(combined) <= target_count:
        return combined

    # Trim: remove lowest-scoring non-role cards first
    trim = len(combined) - target_count
    scored = sorted(
        combined,
        key=lambda c: (bool(tag_card_roles(c)), score_card(c, sfmt)),
    )
    remove_names = {(c.get("name_en") or "").lower() for c in scored[:trim]}
    return [c for c in combined if (c.get("name_en") or "").lower() not in remove_names]


# ── Non-basic land base builder ────────────────────────────────────────────────

def _build_land_base(
    pool: list[dict],
    ci: frozenset,
    nonland_deck: list[dict],
    target_lands: int,
    fmt: str,
) -> dict:
    """Return nonbasic_lands, basics_from_collection, basics_missing."""
    max_nonbasics = 14 if fmt == "commander" else 8

    # Collect non-basic lands within color identity
    nonbasic_candidates: list[dict] = []
    for card in pool:
        tl = card.get("type_line") or ""
        if "Land" not in tl or "Basic" in tl:
            continue
        if fmt != "commander" and not _is_pool_eligible(card, fmt):
            continue
        card_ci = color_identity(card)
        if card_ci and not card_ci.issubset(ci):
            continue
        nonbasic_candidates.append(card)

    # Deduplicate by name
    by_name: dict[str, dict] = {}
    for c in nonbasic_candidates:
        name = (c.get("name_en") or "").lower()
        if name not in by_name:
            by_name[name] = c

    def _land_priority(c: dict) -> float:
        name   = (c.get("name_en") or "").lower()
        oracle = (c.get("oracle_text") or "").lower()
        s = 0.0
        if "command tower" in name:
            s += 20.0
        if "mana of any color" in oracle or "add one mana of any color" in oracle:
            s += 5.0
        # Count how many colors in CI this land can produce
        produce = sum(1 for col in "WUBRG" if f"{{{col}}}" in oracle or f"add {{{col}}}" in oracle)
        s += produce * 1.5
        # Untapped lands are better
        if "enters the battlefield untapped" in oracle or ("untapped" in oracle and "enters" in oracle):
            s += 3.0
        elif "unless" in oracle or "you may pay" in oracle:
            s += 1.5  # shock/check land
        # Fetches
        if "search your library for" in oracle and "land" in oracle:
            s += 8.0
        s += min(c.get("price_eur") or 0.0, 10.0) * 0.4
        return s

    selected_nonbasics = sorted(by_name.values(), key=_land_priority, reverse=True)[:max_nonbasics]

    # Pip-weighted basics
    basics_needed = max(0, target_lands - len(selected_nonbasics))
    pip_counts = _count_mana_pips(nonland_deck)
    desired_basics: dict[str, int] = {}

    if ci and basics_needed > 0:
        split = _pip_weighted_land_split(pip_counts, ci, basics_needed)
        for color, count in split.items():
            land = COLOR_BASICS.get(color)
            if land and count > 0:
                desired_basics[land] = count
    elif basics_needed > 0:
        desired_basics["Wastes"] = basics_needed

    basics_from_collection, basics_missing = _take_basics_from_pool(pool, desired_basics)

    return {
        "nonbasic_lands":         selected_nonbasics,
        "basics_from_collection": basics_from_collection,
        "basics_missing":         basics_missing,
    }


# ── Basic land pool helper ─────────────────────────────────────────────────────

def _take_basics_from_pool(
    pool: list[dict], needed: dict[str, int]
) -> tuple[list[dict], dict[str, int]]:
    still_needed = {k: v for k, v in needed.items() if v > 0}
    taken: list[dict] = []
    for card in pool:
        if not still_needed:
            break
        name = card.get("name_en") or ""
        if name in still_needed:
            taken.append(card)
            still_needed[name] -= 1
            if still_needed[name] == 0:
                del still_needed[name]
    return taken, still_needed


# ── Commander ─────────────────────────────────────────────────────────────────

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


def _build_commander_for_archetype(
    commander: dict,
    pool: list[dict],
    archetype: str,
    power_level: str,
    max_price: Optional[float],
) -> dict:
    from core.analysis import select_for_curve, deck_synergy_score, score_card, detect_archetypes

    ci         = color_identity(commander)
    cmd_themes = get_card_themes(commander)
    cmd_name   = (commander.get("name_en") or "").lower()

    target_nonland = 63
    target_lands   = 36

    # Eligible non-land cards
    eligible_base = [
        c for c in pool
        if (c.get("name_en") or "").lower() != cmd_name
        and "Land" not in (c.get("type_line") or "")
        and color_identity(c).issubset(ci)
        and is_legal(c, "commander")
    ]
    eligible = _apply_power_level_filter(eligible_base, power_level, max_price)

    def _score(c: dict) -> float:
        return (
            score_card(c, "commander")
            + len(get_card_themes(c) & cmd_themes) * 8.0
            + _commander_synergy_score(c, commander)
        )

    # Deduplicate by name, keep best-scored copy
    by_name: dict[str, dict] = {}
    for c in eligible:
        name = (c.get("name_en") or "").lower()
        if name not in by_name or _score(c) > _score(by_name[name]):
            by_name[name] = c
    unique_eligible = sorted(by_name.values(), key=_score, reverse=True)

    # --- Role slots ---
    chosen_names: set[str] = set()
    role_cards, _ = _fill_role_slots(unique_eligible, dict(_COMMANDER_ROLE_TARGETS), chosen_names)

    # --- Theme + curve cards ---
    remaining_pool = [c for c in unique_eligible if (c.get("name_en") or "").lower() not in chosen_names]
    remaining_target = max(1, target_nonland - len(role_cards))
    theme_cards = select_for_curve(remaining_pool[:90], archetype, remaining_target, fmt="commander")

    # Combine without duplicates
    deck = list(role_cards)
    deck_names: set[str] = {(c.get("name_en") or "").lower() for c in deck}
    for c in theme_cards:
        name = (c.get("name_en") or "").lower()
        if name not in deck_names and len(deck) < target_nonland:
            deck.append(c)
            deck_names.add(name)

    # --- Diversity enforcement ---
    deck = _enforce_diversity(deck, archetype, unique_eligible, target_nonland, "commander")

    # --- Land base ---
    land_base = _build_land_base(pool, ci, deck, target_lands, "commander")
    nonbasic_lands         = land_base["nonbasic_lands"]
    basics_from_collection = land_base["basics_from_collection"]
    basics_missing         = land_base["basics_missing"]

    # Trim to exactly 99 (1 cmd + nonland + all lands)
    total_lands = (
        len(nonbasic_lands)
        + len(basics_from_collection)
        + sum(basics_missing.values())
    )
    total = 1 + len(deck) + total_lands
    if total > 99:
        over = total - 99
        deck_scored = sorted(deck, key=_score)
        cut_names = {(c.get("name_en") or "").lower() for c in deck_scored[:over]}
        deck = [c for c in deck if (c.get("name_en") or "").lower() not in cut_names]

    # Build output
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

    return {
        "commander":              commander,
        "deck":                   deck,
        "nonbasic_lands":         nonbasic_lands,
        "basics_from_collection": basics_from_collection,
        "basics_missing":         basics_missing,
        "groups":                 groups,
        "themes":                 top_themes,
        "collection_count":       len(all_cards),
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
) -> dict:
    """Build commander deck; returns primary result with up to 3 archetype variants."""
    from core.analysis import detect_archetypes

    ci = color_identity(commander)
    sample = [
        c for c in pool
        if "Land" not in (c.get("type_line") or "")
        and color_identity(c).issubset(ci)
        and is_legal(c, "commander")
    ]
    archetypes = detect_archetypes([commander] + sample)

    variants: list[dict] = []
    for arch, conf in archetypes[:3]:
        if conf < 0.15 and variants:
            break
        result = _build_commander_for_archetype(commander, pool, arch, power_level, max_price)
        result["archetype_confidence"] = round(conf, 3)
        variants.append(result)

    if not variants:
        result = _build_commander_for_archetype(commander, pool, "default", power_level, max_price)
        result["archetype_confidence"] = 1.0
        variants = [result]

    primary = variants[0]
    primary["variants"]  = variants
    primary["archetypes"] = archetypes[:3]
    return primary


# ── Timeless / Standard ────────────────────────────────────────────────────────

def get_available_strategies(pool: list[dict]) -> list[tuple[str, str, int]]:
    theme_hits: Counter = Counter()
    for c in pool:
        for t in get_card_themes(c):
            theme_hits[t] += 1
    return [
        (t, t.replace("tribal_", "").title(), theme_hits[t])
        for t, _ in theme_hits.most_common()
        if theme_hits[t] > 0
    ]


def _build_60_core(
    pool: list[dict],
    fmt: str,
    archetype: str,
    theme_key: Optional[str],
    power_level: str,
    max_price: Optional[float],
) -> dict:
    """Internal 60-card build for a specific archetype (no variants)."""
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

    # Role slots
    chosen_names: set[str] = set()
    role_cards, _ = _fill_role_slots(unique_nonland, dict(_60_ROLE_TARGETS), chosen_names)

    # Theme + curve
    theme_keys = {theme_key} if theme_key else set()
    if not theme_keys:
        theme_hits: Counter = Counter()
        for c in legal_nonland:
            for t in get_card_themes(c):
                theme_hits[t] += 1
        if theme_hits:
            theme_keys = {theme_hits.most_common(1)[0][0]}

    remaining_unique = [c for c in unique_nonland if (c.get("name_en") or "").lower() not in chosen_names]
    themed  = [c for c in remaining_unique if theme_keys and any(t in get_card_themes(c) for t in theme_keys)]
    others  = [c for c in remaining_unique if c not in themed]
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

    # Diversity
    all_unique = list(name_first.values())
    selected_unique = _enforce_diversity(selected_unique, archetype, all_unique, target_nonland, "60")

    # Expand to (card, count) with copy limits
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

    # Color identity from deck
    colors_used: set[str] = set()
    for card, _ in deck_cards:
        colors_used |= color_identity(card)
    ci = frozenset(colors_used)

    # Land base
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
    """Build a 60-card deck with improved land base and role guarantees.

    Returns primary result with up to 3 variants when strategy is auto-detected.
    """
    from core.analysis import detect_archetypes

    legal_nonland = [
        c for c in pool
        if _is_pool_eligible(c, fmt) and "Land" not in (c.get("type_line") or "")
    ]

    if forced_strategy:
        archetype = _THEME_TO_ARCH.get(forced_strategy, forced_strategy.replace("tribal_", "").title())
        archetypes = [(archetype, 1.0)]
    else:
        archetypes = detect_archetypes(legal_nonland)
        archetype  = archetypes[0][0] if archetypes else "Midrange"

    primary_theme = forced_strategy or _ARCH_TO_THEME.get(archetype)
    primary = _build_60_core(pool, fmt, archetype, primary_theme, power_level, max_price)
    primary["archetype_confidence"] = archetypes[0][1] if archetypes else 1.0

    # Build up to 2 additional variants for auto-detect mode
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


# ── Iterative refinement ─────────────────────────────────────────────────────

def iterative_refine(
    result: dict,
    pool: list[dict],
    max_iterations: int = 5,
    weak_fraction: float = 0.15,
) -> dict:
    """Hill-climb a deck result by repeatedly swapping low-fit cards for better ones.

    Each iteration: score all deck non-land cards, identify the bottom
    *weak_fraction* that aren't filling critical role gaps, pre-score all
    unused pool candidates, then swap each weak card for the best available
    improvement (minimum +3 fit points). Stops when no swaps are made or
    *max_iterations* is reached.

    Returns a shallow-copied result with updated deck, land base, stats, and:
      - refinement_iterations: int
      - refinement_swaps: int
      - refinement_log: list[str]
    """
    from core.analysis import card_deck_fit_score, deck_synergy_score

    is_commander = bool(result.get("commander"))
    commander    = result.get("commander") if is_commander else None
    archetype    = result.get("archetype", "default")
    power_level  = result.get("power_level", "focused")
    fmt_key      = "commander" if is_commander else result.get("format", "timeless")
    ana_fmt      = "commander" if is_commander else "60"

    # Normalise to list[dict] for the loop
    raw_deck = result.get("deck", [])
    if raw_deck and isinstance(raw_deck[0], (list, tuple)):
        deck = [card for card, _count in raw_deck]
        is_60 = True
    else:
        deck = list(raw_deck)
        is_60 = False

    # Color-identity / legality filter
    if is_commander:
        ci = color_identity(commander)
        def _eligible(c: dict) -> bool:
            return color_identity(c).issubset(ci) and is_legal(c, "commander")
    else:
        def _eligible(c: dict) -> bool:
            return _is_pool_eligible(c, fmt_key)

    role_targets = dict(_COMMANDER_ROLE_TARGETS if is_commander else _60_ROLE_TARGETS)

    # Unused non-land pool candidates (deduplicated by name)
    deck_names: set[str] = {(c.get("name_en") or "").lower() for c in deck}
    if commander:
        deck_names.add((commander.get("name_en") or "").lower())

    unused_base = _apply_power_level_filter(
        [c for c in pool
         if "Land" not in (c.get("type_line") or "")
         and _eligible(c)
         and (c.get("name_en") or "").lower() not in deck_names],
        power_level, None,
    )
    unused: dict[str, dict] = {}
    for c in unused_base:
        name = (c.get("name_en") or "").lower()
        if name not in unused:
            unused[name] = c

    log: list[str] = []
    total_swaps = 0

    for iteration in range(max_iterations):
        if not unused:
            log.append(f"Iteration {iteration + 1}: pool exhausted")
            break

        # Current role coverage
        role_counts: Counter = Counter()
        for c in deck:
            for r in tag_card_roles(c):
                role_counts[r] += 1
        role_gaps = {r: max(0, role_targets.get(r, 0) - role_counts.get(r, 0)) for r in role_targets}

        # Pre-compute deck curve for this iteration
        deck_curve: dict[int, int] = {}
        for c in deck:
            b = min(int(c.get("cmc") or 0), 6)
            deck_curve[b] = deck_curve.get(b, 0) + 1

        def _fit(c: dict) -> float:
            return card_deck_fit_score(c, deck, archetype, commander, ana_fmt,
                                       role_gaps, _deck_curve=deck_curve)

        # Score each deck card
        scored = sorted([(c, _fit(c)) for c in deck], key=lambda x: x[1])

        # Identify replaceable weak cards: bottom fraction, not filling critical roles
        n_weak = max(1, int(len(deck) * weak_fraction))
        replaceable = [
            (c, sc) for c, sc in scored
            if not any(
                r in tag_card_roles(c) and role_gaps.get(r, 0) > 0
                for r in role_targets
            )
        ][:n_weak]

        if not replaceable:
            log.append(f"Iteration {iteration + 1}: all weak slots protected by role needs")
            break

        # Pre-score all unused candidates once for this iteration
        cand_scores = sorted(
            [(name, c, _fit(c)) for name, c in unused.items()],
            key=lambda x: x[2],
            reverse=True,
        )

        swaps = 0
        used_this_iter: set[str] = set()

        for weak_card, weak_score in replaceable:
            weak_name = (weak_card.get("name_en") or "").lower()
            threshold = weak_score + 3.0

            best = next(
                ((nm, c, sc) for nm, c, sc in cand_scores
                 if nm not in used_this_iter and sc > threshold),
                None,
            )
            if best is None:
                continue

            cand_name, best_cand, _ = best
            deck = [best_cand if c is weak_card else c for c in deck]
            deck_names.discard(weak_name)
            deck_names.add(cand_name)
            del unused[cand_name]
            unused[weak_name] = weak_card
            used_this_iter.add(cand_name)
            swaps += 1

        total_swaps += swaps
        log.append(f"Iteration {iteration + 1}: {swaps} swap{'s' if swaps != 1 else ''}")
        if swaps == 0:
            break

    result = dict(result)
    result["refinement_iterations"] = sum(1 for l in log if "swap" in l)
    result["refinement_swaps"] = total_swaps
    result["refinement_log"] = log

    if total_swaps == 0:
        result["refinement_log"] = ["Already optimal — no improvements found"]
        return result

    # Rebuild stats with updated deck
    if is_60:
        avail: Counter = Counter()
        for c in pool:
            nm = (c.get("name_en") or "").lower()
            if _eligible(c):
                avail[nm] += 1
        new_deck: list[tuple[dict, int]] = []
        for c in deck:
            copies = min(avail.get((c.get("name_en") or "").lower(), 1), 4)
            new_deck.append((c, copies))
        result["deck"] = new_deck
        role_summary: Counter = Counter()
        for c, _ in new_deck:
            for r in tag_card_roles(c):
                role_summary[r] += 1
        result["curve"] = curve_analysis(new_deck)
        result["synergy_score"] = deck_synergy_score([c for c, _ in new_deck[:30]])
        result["collection_count"] = (
            sum(n for _, n in new_deck)
            + len(result.get("nonbasic_lands", []))
            + len(result.get("basics_from_collection", []))
        )
        result["value_eur"] = round(
            sum((c.get("price_eur") or 0) * n for c, n in new_deck)
            + sum(c.get("price_eur") or 0 for c in result.get("nonbasic_lands", [])),
            2,
        )
    else:
        result["deck"] = deck
        groups: dict[str, list[dict]] = {}
        for c in deck:
            groups.setdefault(_type_group(c), []).append(c)
        result["groups"] = groups

        # Rebuild land base (deck composition may have shifted pip weights)
        if commander:
            ci = color_identity(commander)
        else:
            ci_set: set[str] = set()
            for c in deck:
                ci_set |= set(color_identity(c))
            ci = frozenset(ci_set)
        land_base = _build_land_base(pool, ci, deck, 36, "commander")
        result["nonbasic_lands"]         = land_base["nonbasic_lands"]
        result["basics_from_collection"] = land_base["basics_from_collection"]
        result["basics_missing"]         = land_base["basics_missing"]

        role_summary = Counter()
        for c in deck:
            for r in tag_card_roles(c):
                role_summary[r] += 1
        result["curve"] = curve_analysis([(c, 1) for c in deck])
        result["synergy_score"] = deck_synergy_score(deck[:40])
        all_cards = deck + result["nonbasic_lands"] + result["basics_from_collection"]
        result["collection_count"] = len(all_cards)
        result["value_eur"] = round(sum(c.get("price_eur") or 0 for c in all_cards), 2)

    result["role_summary"] = dict(role_summary)
    return result


# ── Deck list formatting ───────────────────────────────────────────────────────

def _display_name(card: dict) -> str:
    en  = card.get("name_en") or "?"
    loc = card.get("printed_name") or card.get("name_de") or en
    if loc and loc != en:
        return f"{loc}  // EN: {en}"
    return en


def _location_manifest(cards: list[dict]) -> str:
    rows = []
    for c in cards:
        if not c.get("id"):
            continue
        en  = c.get("name_en") or "?"
        loc = c.get("printed_name") or c.get("name_de") or en
        name_col = f"{loc} / {en}" if loc != en else en
        rows.append((
            str(c.get("id") or "—"),
            str(c.get("container_id") or "—"),
            c.get("container_name") or "—",
            name_col,
        ))
    if not rows:
        return ""
    w_cid  = max(len(r[0]) for r in rows)
    w_ctid = max(len(r[1]) for r in rows)
    w_ct   = max(len(r[2]) for r in rows)
    header = f"{'Card ID':<{w_cid}}  {'Cont. ID':<{w_ctid}}  {'Container':<{w_ct}}  Card"
    sep    = "-" * (w_cid + 2 + w_ctid + 2 + w_ct + 2 + 40)
    lines  = ["", "// --- Location Manifest ---",
              "// Original card locations at time of proposal", header, sep]
    for card_id, cont_id, cont_name, name in rows:
        lines.append(f"{card_id:<{w_cid}}  {cont_id:<{w_ctid}}  {cont_name:<{w_ct}}  {name}")
    return "\n".join(lines)


def format_commander_decklist(result: dict) -> str:
    cmd = result["commander"]
    power_level = result.get("power_level", "focused")
    arch        = result.get("archetype", "")
    synergy     = result.get("synergy_score", 0)
    roles       = result.get("role_summary", {})
    role_str    = "  ".join(
        f"{r.title()}: {n}"
        for r, n in sorted(roles.items())
        if r in ("ramp", "removal", "draw") and n
    )
    lines = [
        f"// Commander — {arch}  [{power_level}]  Synergy: {synergy:.1f}",
        f"// Roles: {role_str}" if role_str else "",
        "",
        "Commander",
        f"1 {_display_name(cmd)}  // \U0001f4e6 {cmd.get('container_name') or '—'}",
        "",
    ]
    lines = [l for l in lines if l is not None]  # keep empties but not None

    for group, cards in sorted(result["groups"].items()):
        lines.append(group)
        for c in cards:
            lines.append(f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}")
        lines.append("")

    nonbasic_lands         = result.get("nonbasic_lands") or []
    basics_from_collection = result.get("basics_from_collection") or []
    basics_missing         = result.get("basics_missing") or {}

    lines.append("Lands")
    for c in nonbasic_lands:
        lines.append(f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}")
    for c in basics_from_collection:
        lines.append(f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}")
    for land, n in sorted(basics_missing.items()):
        lines.append(f"{n} {land}  // ⚠ not in collection")

    all_cards = (
        ([result["commander"]] if result["commander"].get("id") else [])
        + result["deck"]
        + nonbasic_lands
        + basics_from_collection
    )
    manifest = _location_manifest(all_cards)
    if manifest:
        lines.append(manifest)
    return "\n".join(lines)


def format_commander_decklist_mtga(result: dict) -> str:
    cmd = result["commander"]
    lines = ["Commander", _mtga_line(cmd), "", "Deck"]
    for _group, cards in sorted(result["groups"].items()):
        for c in cards:
            lines.append(_mtga_line(c))
    for c in result.get("nonbasic_lands") or []:
        lines.append(_mtga_line(c))
    for c in result.get("basics_from_collection") or []:
        lines.append(_mtga_line(c))
    for land, n in sorted((result.get("basics_missing") or {}).items()):
        lines.append(f"{n} {land}")
    return "\n".join(lines)


def format_60_decklist(result: dict) -> str:
    fmt         = result["format"].capitalize()
    strategy    = result.get("strategy", "")
    arch        = result.get("archetype", "")
    power_level = result.get("power_level", "focused")
    roles       = result.get("role_summary", {})
    role_str    = "  ".join(
        f"{r.title()}: {n}"
        for r, n in sorted(roles.items())
        if r in ("ramp", "removal", "draw") and n
    )
    lines = [
        f"// {fmt} — Strategy: {strategy}  Archetype: {arch}  [{power_level}]",
        f"// Roles: {role_str}" if role_str else "",
        "",
    ]
    for card, n in result["deck"]:
        lines.append(f"{n} {_display_name(card)}  // \U0001f4e6 {card.get('container_name') or '—'}")

    nonbasic_lands         = result.get("nonbasic_lands") or []
    basics_from_collection = result.get("basics_from_collection") or []
    basics_missing         = result.get("basics_missing") or {}

    if nonbasic_lands or basics_from_collection or basics_missing:
        lines.append("")
        lines.append("// Lands")
        for c in nonbasic_lands:
            lines.append(f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}")
        for c in basics_from_collection:
            lines.append(f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}")
        for land, n in sorted(basics_missing.items()):
            lines.append(f"{n} {land}  // ⚠ not in collection")

    all_cards = [c for c, _ in result["deck"]] + nonbasic_lands + basics_from_collection
    manifest = _location_manifest(all_cards)
    if manifest:
        lines.append(manifest)
    return "\n".join(lines)


def format_60_decklist_mtga(result: dict) -> str:
    lines = ["Deck"]
    for card, n in result["deck"]:
        lines.append(_mtga_line(card, n))
    nonbasic_lands         = result.get("nonbasic_lands") or []
    basics_from_collection = result.get("basics_from_collection") or []
    basics_missing         = result.get("basics_missing") or {}
    if nonbasic_lands or basics_from_collection or basics_missing:
        lines.append("")
    for c in nonbasic_lands:
        lines.append(_mtga_line(c))
    for c in basics_from_collection:
        lines.append(_mtga_line(c))
    for land, n in sorted(basics_missing.items()):
        lines.append(f"{n} {land}")
    return "\n".join(lines)


def _mtga_line(card: dict, count: int = 1) -> str:
    name     = card.get("name_en") or "?"
    set_code = (card.get("set_code") or "").upper()
    cn       = card.get("collector_number") or ""
    if set_code and cn:
        return f"{count} {name} ({set_code}) {cn}"
    return f"{count} {name}"


def format_container_decklist(cards: list[dict], deck_name: str = "", mtga: bool = True) -> str:
    commanders = [c for c in cards if c.get("is_commander")]
    rest       = [c for c in cards if not c.get("is_commander")]
    lines: list[str] = []
    if deck_name:
        lines += [f"// {deck_name}", ""]
    if commanders:
        lines.append("Commander")
        for c in commanders:
            lines.append(
                _mtga_line(c) if mtga
                else f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}"
            )
        lines.append("")
    if rest:
        groups: dict[str, list[dict]] = {}
        for c in rest:
            groups.setdefault(_type_group(c), []).append(c)
        lines.append("Deck")
        for group, group_cards in sorted(groups.items()):
            if not mtga:
                lines.append(f"// {group}")
            for c in group_cards:
                lines.append(
                    _mtga_line(c) if mtga
                    else f"1 {_display_name(c)}  // \U0001f4e6 {c.get('container_name') or '—'}"
                )
            if not mtga:
                lines.append("")
    if not mtga:
        manifest = _location_manifest(commanders + rest)
        if manifest:
            lines.append(manifest)
    return "\n".join(lines)
