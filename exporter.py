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


def to_json(cards: list[dict]) -> str:
    return json.dumps(cards, ensure_ascii=False, indent=2, default=str)
