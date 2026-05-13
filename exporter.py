import csv
import io
import json
from typing import Any

_CSV_FIELDS = [
    "id", "name_en", "name_de", "printed_name",
    "set_code", "set_name", "collector_number", "released_at",
    "rarity", "colors", "color_identity", "mana_cost", "cmc",
    "type_line", "oracle_text", "flavor_text",
    "power", "toughness", "loyalty", "keywords",
    "price_usd", "price_eur", "price_tix",
    "language", "condition", "foil",
    "container_name",
    "notes", "added_by", "added_at", "updated_at",
    "chaos_key",
]

# Moxfield condition labels
_CONDITION_MAP = {
    "NM": "Near Mint",
    "LP": "Lightly Played",
    "MP": "Moderately Played",
    "HP": "Heavily Played",
    "DMG": "Damaged",
}

# Moxfield language codes (ISO 639-1 → Moxfield name)
_LANG_MAP = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "zhs": "Simplified Chinese",
    "zht": "Traditional Chinese",
}


def _flatten(v: Any) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    if v is None:
        return ""
    return str(v)


def to_csv(cards: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_CSV_FIELDS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for card in cards:
        writer.writerow({f: _flatten(card.get(f)) for f in _CSV_FIELDS})
    return buf.getvalue()


def to_moxfield(cards: list[dict]) -> str:
    """Export in Moxfield collection CSV format (importable at moxfield.com)."""
    fields = ["Count", "Name", "Edition", "Condition", "Language", "Foil", "Collector Number"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for card in cards:
        foil_val = "foil" if card.get("foil") else ""
        writer.writerow({
            "Count":            1,
            "Name":             card.get("name_en") or "",
            "Edition":          (card.get("set_code") or "").upper(),
            "Condition":        _CONDITION_MAP.get(card.get("condition", "NM"), "Near Mint"),
            "Language":         _LANG_MAP.get(card.get("language", "en"), "English"),
            "Foil":             foil_val,
            "Collector Number": card.get("collector_number") or "",
        })
    return buf.getvalue()


def to_json(cards: list[dict]) -> str:
    return json.dumps(cards, ensure_ascii=False, indent=2, default=str)
