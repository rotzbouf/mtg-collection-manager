"""Role classification: tag_card_roles, role-slot filling, commander synergy."""
from ._constants import _ROLE_PATTERNS


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

    if cmd_name and len(cmd_name) > 4 and cmd_name in card_oracle:
        score += 5.0

    trigger_map: list[tuple[str, list[str]]] = [
        ("when you cast an instant or sorcery", ["instant", "sorcery"]),
        ("whenever you cast",                   ["instant", "sorcery"]),
        ("whenever a creature dies",            ["sacrifice", "destroy"]),
        ("whenever you gain life",              ["lifelink", "gain life"]),
        ("landfall",                            ["basic land", "land enters", "search your library for a land"]),
        ("whenever a token",                    ["create a", "token"]),
        ("whenever you draw",                   ["draw a card", "scry", "surveil"]),
        ("+1/+1 counter",                       ["proliferate", "put a counter"]),
    ]
    for cmd_trigger, enablers in trigger_map:
        if cmd_trigger in cmd_oracle:
            for enabler in enablers:
                if enabler in card_oracle:
                    score += 1.5

    return min(score, 15.0)
