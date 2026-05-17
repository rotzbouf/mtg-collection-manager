# backward-compat shim — actual implementation is in core/scanner.py
from core.scanner import *  # noqa: F401,F403
from core.scanner import init_ocr, ocr_available, extract_name, extract_collector_info, isolate_card, get_isolated_preview  # noqa: F401
