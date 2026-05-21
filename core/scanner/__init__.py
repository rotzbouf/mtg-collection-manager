"""MTG card scanner — public API.

Internal structure:
  isolation.py  — OpenCV card detection and perspective warp
  ocr.py        — EasyOCR / Tesseract engines, zone preprocessing, text extraction
  extractor.py  — High-level pipeline (extract_collector_info, get_isolated_preview)
"""
from .isolation import isolate_card, MAX_INPUT_BYTES
from .ocr import init_ocr, ocr_available, extract_name
from .extractor import extract_collector_info, get_isolated_preview

__all__ = [
    "isolate_card",
    "MAX_INPUT_BYTES",
    "init_ocr",
    "ocr_available",
    "extract_name",
    "extract_collector_info",
    "get_isolated_preview",
]
