"""Iterative hill-climbing refinement for built decks."""
from collections import Counter

from ._cards import _is_pool_eligible, is_legal, color_identity, curve_analysis
from ._roles import tag_card_roles
from ._mana import _build_land_base
from ._pool import _apply_power_level_filter
from ._constants import _COMMANDER_ROLE_TARGETS, _60_ROLE_TARGETS


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
    fmt_key      = "commander" if is_commander else result.get("format", "modern")
    ana_fmt      = "commander" if is_commander else "60"

    raw_deck = result.get("deck", [])
    if raw_deck and isinstance(raw_deck[0], (list, tuple)):
        deck = [card for card, _count in raw_deck]
        is_60 = True
    else:
        deck = list(raw_deck)
        is_60 = False

    if is_commander:
        ci = color_identity(commander)
        def _eligible(c: dict) -> bool:
            return color_identity(c).issubset(ci) and is_legal(c, "commander")
    else:
        def _eligible(c: dict) -> bool:
            return _is_pool_eligible(c, fmt_key)

    role_targets = dict(_COMMANDER_ROLE_TARGETS if is_commander else _60_ROLE_TARGETS)

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

        role_counts: Counter = Counter()
        for c in deck:
            for r in tag_card_roles(c):
                role_counts[r] += 1
        role_gaps = {r: max(0, role_targets.get(r, 0) - role_counts.get(r, 0)) for r in role_targets}

        deck_curve: dict[int, int] = {}
        for c in deck:
            b = min(int(c.get("cmc") or 0), 6)
            deck_curve[b] = deck_curve.get(b, 0) + 1

        def _fit(c: dict) -> float:
            return card_deck_fit_score(c, deck, archetype, commander, ana_fmt,
                                       role_gaps, _deck_curve=deck_curve)

        scored = sorted([(c, _fit(c)) for c in deck], key=lambda x: x[1])

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
    result["refinement_swaps"]      = total_swaps
    result["refinement_log"]        = log

    if total_swaps == 0:
        result["refinement_log"] = ["Already optimal — no improvements found"]
        return result

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
        from ._cards import _type_group
        result["deck"] = deck
        groups: dict[str, list[dict]] = {}
        for c in deck:
            groups.setdefault(_type_group(c), []).append(c)
        result["groups"] = groups

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
