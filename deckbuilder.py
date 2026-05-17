# backward-compat shim — actual implementation is in core/deckbuilder.py
from core.deckbuilder import *  # noqa: F401,F403
from core.deckbuilder import (  # noqa: F401
    THEMES, COLOR_BASICS,
    is_legal, color_identity, get_card_themes, is_commander_eligible,
    rank_commanders, build_commander_deck, build_60_deck,
    format_commander_decklist, format_60_decklist,
)
