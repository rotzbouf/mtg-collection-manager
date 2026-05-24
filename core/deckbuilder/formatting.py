"""Deck list formatting: human-readable, MTGA-importable, and location manifest output."""
from collections import defaultdict
from ._cards import _type_group


def _display_name(card: dict) -> str:
    en  = card.get("name_en") or "?"
    loc = card.get("printed_name") or card.get("name_de") or en
    if loc and loc != en:
        return f"{loc}  // EN: {en}"
    return en


def _mtga_line(card: dict, count: int = 1) -> str:
    name     = card.get("name_en") or "?"
    set_code = (card.get("set_code") or "").upper()
    cn       = card.get("collector_number") or ""
    if set_code and cn:
        return f"{count} {name} ({set_code}) {cn}"
    return f"{count} {name}"


# ── Location manifest ──────────────────────────────────────────────────────────

_W = 66  # manifest line width


def _location_manifest(entries: list[tuple[dict, int]]) -> str:
    """
    entries: list of (card_dict, count).
    Groups by container, sorted alphabetically by container name.
    Returns a standalone printable text block (empty string if no entries with IDs).
    """
    groups: dict[tuple[int, str], list[tuple[int, str, str]]] = defaultdict(list)

    for card, count in entries:
        if not card.get("id"):
            continue
        cont_key = (
            card.get("container_id") or 0,
            card.get("container_name") or "— No container —",
        )
        en  = card.get("name_en") or "?"
        loc = card.get("printed_name") or card.get("name_de") or en
        name_col = f"{loc}  //  {en}" if loc != en else en
        groups[cont_key].append((count, str(card.get("id")), name_col))

    if not groups:
        return ""

    n_containers = len(groups)
    n_cards      = sum(c for rows in groups.values() for c, _, _ in rows)

    lines = [
        "=" * _W,
        " LOCATION MANIFEST",
        f" {n_cards} card{'s' if n_cards != 1 else ''} "
        f"across {n_containers} container{'s' if n_containers != 1 else ''}",
        " Pick all cards from each container before moving to the next.",
        "=" * _W,
    ]

    for (_cont_id, cont_name), rows in sorted(
        groups.items(), key=lambda kv: kv[0][1].lower()
    ):
        n = sum(c for c, _, _ in rows)
        header = f"  {cont_name}  ({n} card{'s' if n != 1 else ''})"
        lines.append("")
        lines.append(header)
        lines.append("  " + "-" * (_W - 2))
        for count, card_id, name in sorted(rows, key=lambda r: r[2].lower()):
            qty = f"{count}x " if count > 1 else "   "
            lines.append(f"  {qty}{name}  [#{card_id}]")

    lines.append("")
    lines.append("=" * _W)
    return "\n".join(lines)


def format_location_manifest(result: dict, fmt: str = "commander") -> str:
    """Return a standalone printable location manifest for a build result."""
    entries: list[tuple[dict, int]] = []

    if fmt == "commander":
        cmd = result.get("commander") or {}
        if cmd.get("id"):
            entries.append((cmd, 1))
        for c in result.get("deck") or []:
            if c.get("id"):
                entries.append((c, 1))
    else:
        # Prefer deck_physical: individual physical copies with correct per-copy
        # container attribution (cards with count>1 may span multiple containers).
        physical = result.get("deck_physical")
        if physical:
            for c in physical:
                if c.get("id"):
                    entries.append((c, 1))
        else:
            for card, count in result.get("deck") or []:
                if card.get("id"):
                    entries.append((card, count))

    for c in result.get("nonbasic_lands") or []:
        if c.get("id"):
            entries.append((c, 1))
    for c in result.get("basics_from_collection") or []:
        if c.get("id"):
            entries.append((c, 1))

    manifest = _location_manifest(entries)

    basics_missing = result.get("basics_missing") or {}
    if basics_missing:
        extra = ["\n  Basics to acquire (not in collection):"]
        for land, n in sorted(basics_missing.items()):
            extra.append(f"    {n}x {land}")
        return manifest + "\n" + "\n".join(extra)

    return manifest


# ── Plain-text decklist formatters ─────────────────────────────────────────────

def format_commander_decklist(result: dict) -> str:
    cmd         = result["commander"]
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
    return "\n".join(lines)


def format_container_location_manifest(cards: list[dict], deck_name: str = "") -> str:
    """Standalone manifest for a deck container (deck_analysis export)."""
    entries = [(c, 1) for c in cards if c.get("id")]
    manifest = _location_manifest(entries)
    if deck_name and manifest:
        header = f"Deck: {deck_name}\n"
        return header + manifest
    return manifest
