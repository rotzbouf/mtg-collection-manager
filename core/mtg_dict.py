"""MTG multilingual term dictionary.

Maps common non-English MTG terms to their English equivalents so that
type-line and oracle-text searches work regardless of the user's input language.

Currently covers German (the primary non-English language in this collection).
Add more languages by extending the respective DE_TO_EN-style dicts and the
TRANSLATIONS list at the bottom.
"""
from __future__ import annotations

# ── German → English ──────────────────────────────────────────────────────────
_DE_TO_EN: dict[str, str] = {
    # Card types
    "kreatur":          "creature",
    "kreaturen":        "creature",
    "zauberei":         "sorcery",
    "spontanzauber":    "instant",
    "verzauberung":     "enchantment",
    "verzauberungen":   "enchantment",
    "artefakt":         "artifact",
    "artefakte":        "artifact",
    "planeswalker":     "planeswalker",
    "land":             "land",
    "ländereien":       "land",
    "stammesmagie":     "tribal",
    # Supertypes
    "legendär":         "legendary",
    "legendäre":        "legendary",
    "legendäres":       "legendary",
    "legendären":       "legendary",
    "legendärer":       "legendary",
    "basis":            "basic",
    "grundland":        "basic land",
    "schnee":           "snow",
    "welt":             "world",
    # Creature subtypes (common)
    "engel":            "angel",
    "erzengel":         "archangel",
    "dämon":            "demon",
    "teufel":           "devil",
    "drache":           "dragon",
    "drachen":          "dragon",
    "zombie":           "zombie",
    "zombies":          "zombie",
    "skelett":          "skeleton",
    "vampir":           "vampire",
    "vampiren":         "vampire",
    "werwolf":          "werewolf",
    "wolf":             "wolf",
    "wölfe":            "wolf",
    "goblin":           "goblin",
    "goblins":          "goblin",
    "elfe":             "elf",
    "elfen":            "elf",
    "zauberer":         "wizard",
    "hexer":            "warlock",
    "hexe":             "witch",
    "hexen":            "witch",
    "ritter":           "knight",
    "soldat":           "soldier",
    "soldaten":         "soldier",
    "krieger":          "warrior",
    "kriegerIn":        "warrior",
    "priester":         "cleric",
    "druide":           "druid",
    "druiden":          "druid",
    "schurke":          "rogue",
    "schurken":         "rogue",
    "schamane":         "shaman",
    "schamanen":        "shaman",
    "barbar":           "barbarian",
    "bestie":           "beast",
    "bestien":          "beast",
    "riese":            "giant",
    "riesen":           "giant",
    "golem":            "golem",
    "geist":            "spirit",
    "geister":          "spirit",
    "gespenst":         "specter",
    "phönix":           "phoenix",
    "hydra":            "hydra",
    "hydren":           "hydra",
    "sphinx":           "sphinx",
    "sphinx":           "sphinx",
    "vogel":            "bird",
    "vögel":            "bird",
    "insekt":           "insect",
    "insekten":         "insect",
    "spinne":           "spider",
    "schlange":         "snake",
    "schlangen":        "snake",
    "mensch":           "human",
    "menschen":         "human",
    "zwerg":            "dwarf",
    "zwerge":           "dwarf",
    "gnom":             "gnome",
    "faun":             "satyr",
    "einhorn":          "unicorn",
    "pegasus":          "pegasus",
    "meerjungfrau":     "merfolk",
    "nixe":             "merfolk",
    # Artifact subtypes
    "ausrüstung":       "equipment",
    "fahrzeug":         "vehicle",
    "fahrzeuge":        "vehicle",
    # Enchantment subtypes
    "aura":             "aura",
    "saga":             "saga",
    "klasse":           "class",
    # Spell keywords
    "flugfähigkeit":    "flying",
    "fliegen":          "flying",
    "flug":             "flying",
    "erste angriff":    "first strike",
    "erster angriff":   "first strike",
    "doppelter angriff": "double strike",
    "eile":             "haste",
    "unverwundbarkeit": "indestructible",
    "unverwundbar":     "indestructible",
    "reichweite":       "reach",
    "wachsamkeit":      "vigilance",
    "trampeln":         "trample",
    "bedrohung":        "menace",
    "lebensbindung":    "lifelink",
    "todesberührung":   "deathtouch",
    "hexensicher":      "hexproof",
    "undurchdringbar":  "shroud",
    "schutz":           "protection",
    "provozieren":      "provoke",
    "zerstörungsresistent": "indestructible",
    "aufblitzen":       "flash",
    "zyklus":           "cycling",
    "cycling":          "cycling",
    "rückruf":          "flashback",
    "nachwirkung":      "aftermath",
    "kicker":           "kicker",
    "auftauchen":       "emerge",
    "delve":            "delve",
    "begeisterung":     "devotion",
    "beschwören":       "convoke",
    "eskalation":       "escalate",
    "gegenzauber":      "counterspell",
    "anzeichen":        "omen",
    # Oracle-text common phrases
    "zerstöre":         "destroy",
    "zerstört":         "destroyed",
    "verbannte":        "exile",
    "verbannen":        "exile",
    "ins exil":         "exile",
    "ziehe":            "draw",
    "karte ziehen":     "draw a card",
    "karten ziehen":    "draw cards",
    "lebenspunkte":     "life",
    "lebenspunkt":      "life",
    "mana":             "mana",
    "angreifen":        "attack",
    "angreifer":        "attacker",
    "blockieren":       "block",
    "blocker":          "blocker",
    "schaden":          "damage",
    "zähler":           "counter",
    "+1/+1-zähler":     "+1/+1 counter",
    "-1/-1-zähler":     "-1/-1 counter",
    "token":            "token",
    "spielstein":       "token",
    "spielsteine":      "token",
    "tappt":            "tap",
    "enttappt":         "untap",
    "aktiviere":        "activate",
}

# ── Public API ─────────────────────────────────────────────────────────────────

# All translation tables in priority order
_TABLES: list[dict[str, str]] = [_DE_TO_EN]


def translate(term: str) -> str | None:
    """Return the English equivalent of *term* if a translation exists, else None."""
    low = term.lower().strip()
    for table in _TABLES:
        if low in table:
            return table[low]
    return None


def expand_term(term: str) -> list[str]:
    """Return [term] if no translation, or [term, english_equivalent] if one is found.

    Used to build OR-based LIKE queries that match both the original input and
    the English canonical form stored in the database.
    """
    en = translate(term)
    if en and en.lower() != term.lower():
        return [term, en]
    return [term]
