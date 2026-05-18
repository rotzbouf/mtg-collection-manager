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


# ── Basic land allocation ─────────────────────────────────────────────────────

def _take_basics_from_pool(pool: list[dict], needed: dict[str, int]) -> tuple[list[dict], dict[str, int]]:
    """Pull basic land cards from the pool to fill *needed* slots.

    Returns (taken_cards, still_needed) where still_needed holds counts
    for basics the collection could not fully supply.
    Each entry in *pool* represents one physical card, so iterating once
    naturally caps at the owned quantity.
    """
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

    # Compute desired basics split
    basics_needed = 99 - len(deck)
    desired_basics: dict[str, int] = {}
    if basics_needed > 0:
        colors = sorted(ci) if ci else []
        if colors:
            per = basics_needed // len(colors)
            rem = basics_needed % len(colors)
            for i, col in enumerate(colors):
                land = COLOR_BASICS.get(col)
                if land:
                    desired_basics[land] = per + (1 if i < rem else 0)
        else:
            desired_basics["Wastes"] = basics_needed

    # Fill basics from collection only; track what's still missing
    basics_from_collection, basics_missing = _take_basics_from_pool(pool, desired_basics)

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
        "commander":              commander,
        "deck":                   deck,
        "basics_missing":         basics_missing,
        "basics_from_collection": basics_from_collection,
        "groups":                 groups,
        "themes":                 top_themes,
        "collection_count":       len(deck) + len(basics_from_collection),
        "value_eur":              round(sum(c.get("price_eur") or 0 for c in deck), 2),
    }


# ── Timeless / Standard ────────────────────────────────────────────────────────

def get_available_strategies(pool: list[dict]) -> list[tuple[str, str]]:
    """Return (theme_key, display_name) pairs sorted by card count in pool, highest first."""
    theme_hits: Counter = Counter()
    for c in pool:
        for t in get_card_themes(c):
            theme_hits[t] += 1
    return [
        (t, t.replace("tribal_", "").title())
        for t, _ in theme_hits.most_common()
        if theme_hits[t] > 0
    ]


def build_60_deck(pool: list[dict], fmt: str, forced_strategy: str | None = None) -> dict:
    """Build a 60-card deck (36 non-lands + 24 basics) for timeless or standard."""
    legal_nonland = [
        c for c in pool
        if is_legal(c, fmt) and "Land" not in (c.get("type_line") or "")
    ]

    # Detect dominant strategy (or use forced one)
    if forced_strategy:
        strategy = forced_strategy
    else:
        theme_hits: Counter = Counter()
        for c in legal_nonland:
            for t in get_card_themes(c):
                theme_hits[t] += 1
        strategy = theme_hits.most_common(1)[0][0] if theme_hits else "goodstuff"

    themed = [c for c in legal_nonland if strategy in get_card_themes(c)]
    others = [c for c in legal_nonland if c not in themed]
    themed.sort(key=lambda c: int(c.get("cmc") or 0))
    others.sort(key=lambda c: int(c.get("cmc") or 0))

    # Pre-count physical copies per card name — this is the true availability.
    # Themed cards are iterated first so they fill name_first before others.
    available: Counter = Counter()
    name_first: dict[str, dict] = {}
    for card in (themed + others):
        name = (card.get("name_en") or "").lower()
        available[name] += 1
        if name not in name_first:
            name_first[name] = card

    # Build deck: themed-first order; copies capped by owned count and format limit (4).
    deck_cards: list[tuple[dict, int]] = []
    total = 0
    for name, card in name_first.items():
        if total >= 36:
            break
        take = min(available[name], 4, 36 - total)
        deck_cards.append((card, take))
        total += take

    colors_used: set[str] = set()
    for card, _ in deck_cards:
        colors_used |= color_identity(card)

    desired_basics: dict[str, int] = {}
    if colors_used:
        per = 24 // len(colors_used)
        rem = 24 % len(colors_used)
        for i, col in enumerate(sorted(colors_used)):
            land = COLOR_BASICS.get(col)
            if land:
                desired_basics[land] = per + (1 if i < rem else 0)
    else:
        desired_basics["Wastes"] = 24

    basics_from_collection, basics_missing = _take_basics_from_pool(pool, desired_basics)

    return {
        "deck":                   deck_cards,
        "basics_missing":         basics_missing,
        "basics_from_collection": basics_from_collection,
        "strategy":               strategy.replace("tribal_", "").title(),
        "format":                 fmt,
        "collection_count":       sum(n for _, n in deck_cards) + len(basics_from_collection),
        "value_eur":              round(sum((c.get("price_eur") or 0) * n for c, n in deck_cards), 2),
    }


# ── Deck list formatting ───────────────────────────────────────────────────────

def _display_name(card: dict) -> str:
    """Return the card's localized name with English fallback annotation.

    If the card has a non-English printed name, show that as the primary name
    and append '// EN: <english>' so the line stays importable into other apps.
    """
    en   = card.get("name_en") or "?"
    loc  = card.get("printed_name") or card.get("name_de") or en
    if loc and loc != en:
        return f"{loc}  // EN: {en}"
    return en


def _location_manifest(cards: list[dict]) -> str:
    """Tabular manifest: card ID, container ID, container name, card name.

    Used as a picking reference — records where each card lives at proposal
    time so the location is known after cards are moved to a deck container.
    """
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
    header = (
        f"{'Card ID':<{w_cid}}  {'Cont. ID':<{w_ctid}}  {'Container':<{w_ct}}  Card (localized / EN)"
    )
    sep = "-" * (w_cid + 2 + w_ctid + 2 + w_ct + 2 + 40)
    lines = ["", "// --- Location Manifest ---",
             "// Original card locations at time of proposal", header, sep]
    for card_id, cont_id, cont_name, name in rows:
        lines.append(
            f"{card_id:<{w_cid}}  {cont_id:<{w_ctid}}  {cont_name:<{w_ct}}  {name}"
        )
    return "\n".join(lines)


def format_commander_decklist(result: dict) -> str:
    cmd = result["commander"]
    cmd_container = cmd.get("container_name") or "—"
    lines = ["Commander", f"1 {_display_name(cmd)}  // 📦 {cmd_container}", ""]
    for group, cards in sorted(result["groups"].items()):
        lines.append(group)
        for c in cards:
            container = c.get("container_name") or "—"
            lines.append(f"1 {_display_name(c)}  // 📦 {container}")
        lines.append("")
    coll_basics = result.get("basics_from_collection") or []
    missing_basics = result.get("basics_missing") or {}
    if coll_basics or missing_basics:
        lines.append("Basic Lands")
        for c in coll_basics:
            container = c.get("container_name") or "—"
            lines.append(f"1 {_display_name(c)}  // 📦 {container}")
        for land, n in sorted(missing_basics.items()):
            lines.append(f"{n} {land}  // ⚠ not in collection")

    all_cards = (
        [result["commander"]] if result["commander"].get("id") else []
    ) + result["deck"] + coll_basics
    manifest = _location_manifest(all_cards)
    if manifest:
        lines.append(manifest)
    return "\n".join(lines)


def format_60_decklist(result: dict) -> str:
    fmt = result["format"].capitalize()
    lines = [f"// {fmt} — Strategy: {result['strategy']}", ""]
    for card, n in result["deck"]:
        container = card.get("container_name") or "—"
        lines.append(f"{n} {_display_name(card)}  // 📦 {container}")
    coll_basics = result.get("basics_from_collection") or []
    missing_basics = result.get("basics_missing") or {}
    if coll_basics or missing_basics:
        lines.append("")
        for c in coll_basics:
            container = c.get("container_name") or "—"
            lines.append(f"1 {_display_name(c)}  // 📦 {container}")
        for land, n in sorted(missing_basics.items()):
            lines.append(f"{n} {land}  // ⚠ not in collection")

    all_cards = [c for c, _ in result["deck"]] + coll_basics
    manifest = _location_manifest(all_cards)
    if manifest:
        lines.append(manifest)
    return "\n".join(lines)


# ── MTGA / Moxfield clean export ──────────────────────────────────────────────

def _mtga_line(card: dict, count: int = 1) -> str:
    name = card.get("name_en") or "?"
    set_code = (card.get("set_code") or "").upper()
    cn = card.get("collector_number") or ""
    if set_code and cn:
        return f"{count} {name} ({set_code}) {cn}"
    return f"{count} {name}"


def format_commander_decklist_mtga(result: dict) -> str:
    """Commander decklist in MTGA/Moxfield import format."""
    cmd = result["commander"]
    lines = ["Commander", _mtga_line(cmd), "", "Deck"]
    for _group, cards in sorted(result["groups"].items()):
        for c in cards:
            lines.append(_mtga_line(c))
    coll_basics = result.get("basics_from_collection") or []
    missing_basics = result.get("basics_missing") or {}
    for c in coll_basics:
        lines.append(_mtga_line(c))
    for land, n in sorted(missing_basics.items()):
        lines.append(f"{n} {land}")
    return "\n".join(lines)


def format_60_decklist_mtga(result: dict) -> str:
    """60-card decklist in MTGA/Moxfield import format."""
    lines = ["Deck"]
    for card, n in result["deck"]:
        lines.append(_mtga_line(card, n))
    coll_basics = result.get("basics_from_collection") or []
    missing_basics = result.get("basics_missing") or {}
    if coll_basics or missing_basics:
        lines.append("")
    for c in coll_basics:
        lines.append(_mtga_line(c))
    for land, n in sorted(missing_basics.items()):
        lines.append(f"{n} {land}")
    return "\n".join(lines)


def format_container_decklist(cards: list[dict], deck_name: str = "", mtga: bool = True) -> str:
    """Export a container's physical cards as a decklist.

    Commander-flagged cards appear under 'Commander', the rest under 'Deck'
    grouped by type. mtga=True produces clean Moxfield/MTGA lines; mtga=False
    adds container-location comments and a picking manifest.
    """
    commanders = [c for c in cards if c.get("is_commander")]
    rest = [c for c in cards if not c.get("is_commander")]

    lines: list[str] = []
    if deck_name:
        lines += [f"// {deck_name}", ""]

    if commanders:
        lines.append("Commander")
        for c in commanders:
            lines.append(_mtga_line(c) if mtga else f"1 {_display_name(c)}  // 📦 {c.get('container_name') or '—'}")
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
                lines.append(_mtga_line(c) if mtga else f"1 {_display_name(c)}  // 📦 {c.get('container_name') or '—'}")
            if not mtga:
                lines.append("")

    if not mtga:
        manifest = _location_manifest(commanders + rest)
        if manifest:
            lines.append(manifest)

    return "\n".join(lines)
