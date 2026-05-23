"""
Deck building engine.

Submodules
----------
_constants   — THEMES, COLOR_BASICS, role targets, diversity minimums, arch/theme maps
_cards       — legality helpers, color_identity, themes, curve, type grouping
_roles       — tag_card_roles, role-slot filling, commander synergy scoring
_mana        — pip counting, pip-weighted land splits, land base construction
_pool        — power/budget filtering, diversity enforcement
commander    — rank_commanders, build_commander_deck
sixty        — build_60_deck (standard, modern, legacy, vintage, pauper)
refinement   — iterative_refine
formatting   — format_commander_decklist*, format_60_decklist*, format_container_decklist
"""

# ── Public API (full backward compat) ─────────────────────────────────────────

from ._constants import (
    THEMES,
    COLOR_BASICS,
    _ARCH_TO_THEME,
    _THEME_TO_ARCH,
    _COMMANDER_ROLE_TARGETS,
    _60_ROLE_TARGETS,
    _DIVERSITY_MINIMUMS_CMD,
    _DIVERSITY_MINIMUMS_60,
)

from ._cards import (
    is_legal,
    _is_pool_eligible,
    _max_copies,
    color_identity,
    get_card_themes,
    is_commander_eligible,
    _type_group,
    curve_analysis,
    get_available_strategies,
)

from ._roles import (
    tag_card_roles,
    _fill_role_slots,
    _commander_synergy_score,
)

from ._mana import (
    _count_mana_pips,
    _pip_weighted_land_split,
    _build_land_base,
)

from ._pool import (
    _apply_power_level_filter,
    _enforce_diversity,
)

from .commander import (
    rank_commanders,
    build_commander_deck,
)

from .sixty import (
    build_60_deck,
)

from .refinement import (
    iterative_refine,
)

from .formatting import (
    format_commander_decklist,
    format_commander_decklist_mtga,
    format_60_decklist,
    format_60_decklist_mtga,
    format_container_decklist,
    format_location_manifest,
    format_container_location_manifest,
)

__all__ = [
    # Constants
    "THEMES", "COLOR_BASICS",
    "_ARCH_TO_THEME", "_THEME_TO_ARCH",
    "_COMMANDER_ROLE_TARGETS", "_60_ROLE_TARGETS",
    "_DIVERSITY_MINIMUMS_CMD", "_DIVERSITY_MINIMUMS_60",
    # Card helpers
    "is_legal", "_is_pool_eligible", "_max_copies",
    "color_identity", "get_card_themes", "is_commander_eligible",
    "_type_group", "curve_analysis", "get_available_strategies",
    # Role helpers
    "tag_card_roles", "_fill_role_slots", "_commander_synergy_score",
    # Mana helpers
    "_count_mana_pips", "_pip_weighted_land_split", "_build_land_base",
    # Pool helpers
    "_apply_power_level_filter", "_enforce_diversity",
    # Builders
    "rank_commanders", "build_commander_deck",
    "build_60_deck",
    "iterative_refine",
    # Formatters
    "format_commander_decklist", "format_commander_decklist_mtga",
    "format_60_decklist", "format_60_decklist_mtga",
    "format_container_decklist",
    "format_location_manifest", "format_container_location_manifest",
]
