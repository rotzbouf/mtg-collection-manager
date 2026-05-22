"""Mana pip analysis and land base construction."""
from ._cards import _is_pool_eligible, color_identity
from ._constants import COLOR_BASICS


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


def _build_land_base(
    pool: list[dict],
    ci: frozenset,
    nonland_deck: list[dict],
    target_lands: int,
    fmt: str,
) -> dict:
    """Return nonbasic_lands, basics_from_collection, basics_missing."""
    max_nonbasics = 14 if fmt == "commander" else 8

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
        produce = sum(1 for col in "WUBRG" if f"{{{col}}}" in oracle or f"add {{{col}}}" in oracle)
        s += produce * 1.5
        if "enters the battlefield untapped" in oracle or ("untapped" in oracle and "enters" in oracle):
            s += 3.0
        elif "unless" in oracle or "you may pay" in oracle:
            s += 1.5
        if "search your library for" in oracle and "land" in oracle:
            s += 8.0
        s += min(c.get("price_eur") or 0.0, 10.0) * 0.4
        return s

    selected_nonbasics = sorted(by_name.values(), key=_land_priority, reverse=True)[:max_nonbasics]

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
