# backward-compat shim — actual implementation is in core/importer.py
from core.importer import *  # noqa: F401,F403
from core.importer import detect_format, parse_moxfield_csv, parse_full_csv, parse_json, normalize_row  # noqa: F401
