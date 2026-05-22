"""Shared data constants — themes, role targets, archetype maps, diversity minimums."""

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

_ROLE_PATTERNS: dict[str, list[str]] = {
    "ramp": [
        "search your library for a basic land",
        "search your library for a land",
        "land card from your library",
        "additional land",
        "add two mana", "add three mana",
        "mana of any color",
    ],
    "removal": [
        "destroy target", "exile target",
        "destroy all creatures", "exile all creatures",
        "counter target spell", "counter target",
        "damage to any target",
        "-x/-x", "−x/−x",
        "return target", "put on the bottom",
    ],
    "draw": [
        "draw a card", "draw two cards", "draw three cards",
        "draw x cards", "draw cards equal",
        "whenever you draw", "scry ", "surveil ",
    ],
    "board_wipe": [
        "destroy all creatures", "exile all creatures",
        "deals damage to each creature",
        "each creature gets -",
        "all creatures get -",
    ],
    "wincon": [
        "you win the game",
        "extra turn",
        "annihilator",
        "infect",
        "poison counter",
    ],
}

# Guaranteed role-slot targets per format
_COMMANDER_ROLE_TARGETS: dict[str, int] = {
    "ramp":    12,
    "removal": 10,
    "draw":    10,
    "wincon":   4,
}

_60_ROLE_TARGETS: dict[str, int] = {
    "ramp":    4,
    "removal": 6,
    "draw":    4,
}

# Minimum card-type counts per archetype
_DIVERSITY_MINIMUMS_CMD: dict[str, dict[str, int]] = {
    "Aggro":        {"Creatures": 20},
    "Control":      {"Instants": 8,  "Creatures": 6},
    "Midrange":     {"Creatures": 16},
    "Ramp":         {"Creatures": 8,  "Sorceries": 6},
    "Tokens":       {"Creatures": 10, "Sorceries": 6},
    "Graveyard":    {"Creatures": 14},
    "Combo":        {"Instants": 6,   "Sorceries": 6},
    "Spellslinger": {"Instants": 10,  "Sorceries": 8, "Creatures": 8},
    "Voltron":      {"Creatures": 8,  "Artifacts": 8},
    "default":      {"Creatures": 12},
}

_DIVERSITY_MINIMUMS_60: dict[str, dict[str, int]] = {
    "Aggro":        {"Creatures": 16, "Instants": 4},
    "Control":      {"Instants": 10,  "Creatures": 4},
    "Midrange":     {"Creatures": 12},
    "Ramp":         {"Creatures": 6,  "Sorceries": 4},
    "Tokens":       {"Creatures": 8,  "Sorceries": 4},
    "Graveyard":    {"Creatures": 10},
    "Combo":        {"Instants": 6,   "Sorceries": 6},
    "Spellslinger": {"Instants": 8,   "Sorceries": 6, "Creatures": 4},
    "Voltron":      {"Creatures": 4,  "Artifacts": 6},
    "default":      {"Creatures": 8},
}

_THEME_TO_ARCH: dict[str, str] = {
    "tokens": "Tokens", "graveyard": "Graveyard", "control": "Control",
    "ramp": "Ramp", "spellslinger": "Spellslinger", "voltron": "Voltron",
    "burn": "Aggro", "discard": "Graveyard", "sacrifice": "Midrange",
    "counters": "Midrange", "lifegain": "Midrange", "draw": "Control",
    "equipment": "Voltron", "enchantress": "Control", "artifacts": "Midrange",
}

_ARCH_TO_THEME: dict[str, str] = {
    "Aggro": "burn", "Control": "control", "Midrange": "counters",
    "Ramp": "ramp", "Tokens": "tokens", "Graveyard": "graveyard",
    "Combo": "draw", "Spellslinger": "spellslinger", "Voltron": "voltron",
}
