"""
Deck building engine.
Formats: commander, timeless, standard
"""

import json
from collections import Counter

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


# ── Core helpers ───────────────────────────────────────────────────────────────

def is_legal(card: dict, fmt: str) -> bool:
    leg = card.get("legalities") or {}
    if isinstance(leg, str):
        try:
            leg = json.loads(leg)
        except Exception:
            return False
    return leg.get(fmt) == "legal"


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


# ── Commander ─────────────────────────────────────────────────────────────────

def rank_commanders(pool: list[dict]) -> list[tuple[dict, int]]:
    """Return up to 10 (card, synergy_score) pairs, best first."""
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
        results.append((cmd, synergy))
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:10]


def build_commander_deck(commander: dict, pool: list[dict]) -> dict:
    ci         = color_identity(commander)
    cmd_themes = get_card_themes(commander)
    cmd_name   = (commander.get("name_en") or "").lower()

    # 99 slots: aim for ~63 non-lands + 36 basics
    target_nonland = 63

    eligible = [
        c for c in pool
        if (c.get("name_en") or "").lower() != cmd_name
        and "Land" not in (c.get("type_line") or "")
        and color_identity(c).issubset(ci)
        and is_legal(c, "commander")
    ]

    def _score(c: dict) -> int:
        shared   = len(get_card_themes(c) & cmd_themes)
        cmc_pref = max(0, 6 - int(c.get("cmc") or 0))
        return shared * 4 + cmc_pref

    eligible.sort(key=_score, reverse=True)

    seen: set[str] = {cmd_name}
    deck: list[dict] = []
    for c in eligible:
        name = (c.get("name_en") or "").lower()
        if name in seen:
            continue
        seen.add(name)
        deck.append(c)
        if len(deck) >= target_nonland:
            break

    # Fill remaining 99 slots with basic lands (Wastes for colorless commanders)
    basics_needed = 99 - len(deck)
    basics: dict[str, int] = {}
    if basics_needed > 0:
        colors = sorted(ci) if ci else []
        if colors:
            per = basics_needed // len(colors)
            rem = basics_needed % len(colors)
            for i, col in enumerate(colors):
                land = COLOR_BASICS.get(col)
                if land:
                    basics[land] = per + (1 if i < rem else 0)
        else:
            basics["Wastes"] = basics_needed

    # Identify top themes present in the selected cards
    theme_counts: Counter = Counter()
    for c in deck:
        for t in get_card_themes(c) & cmd_themes:
            theme_counts[t] += 1
    top_themes = [t.replace("tribal_", "").title() for t, _ in theme_counts.most_common(5)]

    groups: dict[str, list[dict]] = {}
    for c in deck:
        groups.setdefault(_type_group(c), []).append(c)

    return {
        "commander":        commander,
        "deck":             deck,
        "basics":           basics,
        "groups":           groups,
        "themes":           top_themes,
        "collection_count": len(deck),
        "value_eur":        round(sum(c.get("price_eur") or 0 for c in deck), 2),
    }


# ── Timeless / Standard ────────────────────────────────────────────────────────

def build_60_deck(pool: list[dict], fmt: str) -> dict:
    """Build a 60-card deck (36 non-lands + 24 basics) for timeless or standard."""
    legal_nonland = [
        c for c in pool
        if is_legal(c, fmt) and "Land" not in (c.get("type_line") or "")
    ]

    # Detect dominant strategy
    theme_hits: Counter = Counter()
    for c in legal_nonland:
        for t in get_card_themes(c):
            theme_hits[t] += 1
    strategy = theme_hits.most_common(1)[0][0] if theme_hits else "goodstuff"

    themed = [c for c in legal_nonland if strategy in get_card_themes(c)]
    others = [c for c in legal_nonland if c not in themed]
    themed.sort(key=lambda c: int(c.get("cmc") or 0))
    others.sort(key=lambda c: int(c.get("cmc") or 0))

    name_used: Counter = Counter()
    deck_cards: list[tuple[dict, int]] = []
    total = 0
    for card in (themed + others):
        if total >= 36:
            break
        name  = (card.get("name_en") or "").lower()
        avail = min(1, 4 - name_used[name])   # 1 physical card per row; cap at 4x total
        if avail <= 0:
            continue
        take = min(avail, 36 - total)
        deck_cards.append((card, take))
        name_used[name] += take
        total += take

    colors_used: set[str] = set()
    for card, _ in deck_cards:
        colors_used |= color_identity(card)

    basics: dict[str, int] = {}
    if colors_used:
        per = 24 // len(colors_used)
        rem = 24 % len(colors_used)
        for i, col in enumerate(sorted(colors_used)):
            land = COLOR_BASICS.get(col)
            if land:
                basics[land] = per + (1 if i < rem else 0)
    else:
        basics["Wastes"] = 24

    return {
        "deck":             deck_cards,
        "basics":           basics,
        "strategy":         strategy.replace("tribal_", "").title(),
        "format":           fmt,
        "collection_count": sum(n for _, n in deck_cards),
        "value_eur":        round(sum((c.get("price_eur") or 0) * n for c, n in deck_cards), 2),
    }


# ── Deck list formatting ───────────────────────────────────────────────────────

def format_commander_decklist(result: dict) -> str:
    cmd = result["commander"]
    cmd_container = cmd.get("container_name") or "—"
    lines = ["Commander", f"1 {cmd.get('name_en', '?')}  // 📦 {cmd_container}", ""]
    for group, cards in sorted(result["groups"].items()):
        lines.append(group)
        for c in cards:
            container = c.get("container_name") or "—"
            lines.append(f"1 {c.get('name_en', '?')}  // 📦 {container}")
        lines.append("")
    if result["basics"]:
        lines.append("Basic Lands")
        for land, n in sorted(result["basics"].items()):
            lines.append(f"{n} {land}")
    return "\n".join(lines)


def format_60_decklist(result: dict) -> str:
    fmt = result["format"].capitalize()
    lines = [f"// {fmt} — Strategy: {result['strategy']}", ""]
    for card, n in result["deck"]:
        container = card.get("container_name") or "—"
        lines.append(f"{n} {card.get('name_en', '?')}  // 📦 {container}")
    if result["basics"]:
        lines.append("")
        for land, n in sorted(result["basics"].items()):
            lines.append(f"{n} {land}")
    return "\n".join(lines)
