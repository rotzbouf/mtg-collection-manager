"""Parse import files into card dicts for Database.add_card()."""

import csv
import io
import json

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB

_CONDITION_REV = {
    "Near Mint": "NM", "Lightly Played": "LP", "Moderately Played": "MP",
    "Heavily Played": "HP", "Damaged": "DMG",
    "NM": "NM", "LP": "LP", "MP": "MP", "HP": "HP", "DMG": "DMG",
}

_LANG_REV = {
    "English": "en", "German": "de", "French": "fr", "Italian": "it",
    "Spanish": "es", "Portuguese": "pt", "Japanese": "ja", "Korean": "ko",
    "Russian": "ru", "Simplified Chinese": "zhs", "Traditional Chinese": "zht",
    "en": "en", "de": "de", "fr": "fr", "it": "it", "es": "es",
    "pt": "pt", "ja": "ja", "ko": "ko", "ru": "ru", "zhs": "zhs", "zht": "zht",
}

# Fields that belong to the DB row but must not be forwarded to add_card()
_STRIP_FIELDS = {"id", "added_at", "updated_at", "chaos_key", "color_order", "type_order", "container_name"}


def detect_format(filename: str, content: bytes) -> str:
    """Return 'moxfield_csv', 'full_csv', or 'json'. Raises ValueError on unknown format."""
    fname = filename.lower()
    if fname.endswith(".json"):
        return "json"
    if fname.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        header = next(csv.reader(io.StringIO(text)), [])
        if "Count" in header and "Edition" in header:
            return "moxfield_csv"
        if "name_en" in header:
            return "full_csv"
        raise ValueError("Unrecognised CSV format. Expected a Moxfield CSV or a bot export CSV.")
    raise ValueError("Unsupported file type. Attach a `.csv` or `.json` file.")


def parse_moxfield_csv(content: bytes) -> list[dict]:
    """Parse Moxfield collection CSV into import rows.

    Each row has: name, set_code, collector_number, condition, language, foil (bool), count (int).
    """
    if len(content) > _MAX_IMPORT_BYTES:
        raise ValueError(f"File too large ({len(content) // 1_048_576} MB); maximum is 50 MB.")
    text = content.decode("utf-8-sig", errors="replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        try:
            count = max(1, int(row.get("Count") or 1))
        except ValueError:
            count = 1
        cond_raw = (row.get("Condition") or "NM").strip()
        lang_raw = (row.get("Language") or "English").strip()
        foil = (row.get("Foil") or "").strip().lower() in ("foil", "1", "true", "yes")
        rows.append({
            "name": name,
            "set_code": (row.get("Edition") or "").strip().lower() or None,
            "collector_number": (row.get("Collector Number") or "").strip() or None,
            "condition": _CONDITION_REV.get(cond_raw, "NM"),
            "language": _LANG_REV.get(lang_raw, "en"),
            "foil": foil,
            "count": count,
        })
    return rows


def parse_full_csv(content: bytes) -> list[dict]:
    """Parse the bot's own full CSV export."""
    if len(content) > _MAX_IMPORT_BYTES:
        raise ValueError(f"File too large ({len(content) // 1_048_576} MB); maximum is 50 MB.")
    text = content.decode("utf-8-sig", errors="replace")
    return [r for r in csv.DictReader(io.StringIO(text)) if r.get("name_en")]


def parse_json(content: bytes) -> list[dict]:
    """Parse the bot's own JSON export."""
    if len(content) > _MAX_IMPORT_BYTES:
        raise ValueError(f"File too large ({len(content) // 1_048_576} MB); maximum is 50 MB.")
    data = json.loads(content.decode("utf-8"))
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict) and d.get("name_en")]
    return []


def normalize_row(row: dict) -> tuple[dict, str | None]:
    """Convert a full CSV / JSON row to the format expected by Database.add_card().

    Returns (card_dict, container_name_or_None).
    """
    result = {k: (v if v != "" else None) for k, v in row.items()}

    # Parse JSON-encoded list/dict fields
    for field in ("colors", "color_identity", "keywords"):
        v = result.get(field)
        if isinstance(v, str):
            try:
                result[field] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                result[field] = []
        elif not isinstance(v, list):
            result[field] = []

    if isinstance(result.get("legalities"), str):
        try:
            result["legalities"] = json.loads(result["legalities"])
        except (json.JSONDecodeError, ValueError):
            result["legalities"] = {}
    elif not isinstance(result.get("legalities"), dict):
        result["legalities"] = {}

    result["foil"] = 1 if str(result.get("foil") or "0").strip() in ("1", "true", "yes") else 0

    v = result.get("cmc")
    result["cmc"] = float(v) if v else 0.0
    for field in ("price_usd", "price_eur", "price_tix"):
        v = result.get(field)
        result[field] = float(v) if v else None

    container_name = result.get("container_name") or None
    for f in _STRIP_FIELDS:
        result.pop(f, None)

    return result, container_name
