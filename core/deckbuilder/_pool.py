"""Pool manipulation: budget/power filtering and diversity enforcement."""
from collections import Counter
from typing import Optional

from ._cards import _type_group
from ._roles import tag_card_roles
from ._constants import _DIVERSITY_MINIMUMS_CMD, _DIVERSITY_MINIMUMS_60


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
        # Iterate the full candidates list rather than slicing up-front: the
        # pool may contain multiple physical copies of the same card name that
        # all passed the pre-filter snapshot.  We update deck_names inside the
        # loop so duplicates are skipped as soon as the first copy is taken.
        added_this_type = 0
        for c in candidates:
            if added_this_type >= needed:
                break
            name_c = (c.get("name_en") or "").lower()
            if name_c in deck_names:
                continue          # already have this name (or a dupe physical copy)
            additions.append(c)
            deck_names.add(name_c)
            added_this_type += 1

    if not additions:
        return deck

    combined = deck + additions
    if len(combined) <= target_count:
        return combined

    trim = len(combined) - target_count
    scored = sorted(
        combined,
        key=lambda c: (bool(tag_card_roles(c)), score_card(c, sfmt)),
    )
    remove_names = {(c.get("name_en") or "").lower() for c in scored[:trim]}
    return [c for c in combined if (c.get("name_en") or "").lower() not in remove_names]
