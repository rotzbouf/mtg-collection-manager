"""Deck list formatting: human-readable and MTGA-importable output."""
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
