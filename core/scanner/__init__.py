"""MTG card scanner — public API.

Internal structure:
  debug.py      — ScanTrace dataclass + ContextVar helpers (_step, _trace)
  isolation.py  — OpenCV card detection and perspective warp
  ocr.py        — EasyOCR / Tesseract engines, zone preprocessing, text extraction
  extractor.py  — High-level pipeline (extract_collector_info, get_isolated_preview)

Traced variants
---------------
extract_name_traced(image_bytes) -> (name, ScanTrace)
extract_collector_info_traced(image_bytes) -> (info, ScanTrace)

Both activate a ScanTrace context so that every internal step and OCR
confidence value is captured.  Set SCAN_DEBUG_LOG=<path> to append a
JSON-lines record to a file after each traced scan.
"""
from typing import Optional

from .debug import ScanTrace, SCAN_DEBUG_LOG, _current_trace
from .isolation import isolate_card, MAX_INPUT_BYTES
from .ocr import init_ocr, ocr_available, extract_name
from .extractor import extract_collector_info, get_isolated_preview


def extract_name_traced(image_bytes: bytes) -> tuple[Optional[str], ScanTrace]:
    """Extract card name and return (name, ScanTrace) with full processing log."""
    trace = ScanTrace()
    token = _current_trace.set(trace)
    try:
        name = extract_name(image_bytes)
        trace.extracted_name = name
    finally:
        _current_trace.reset(token)
    trace.log()
    if SCAN_DEBUG_LOG:
        trace.write_to_file(SCAN_DEBUG_LOG)
    return name, trace


def extract_collector_info_traced(image_bytes: bytes) -> tuple[dict, ScanTrace]:
    """Extract footer info and return (info_dict, ScanTrace) with full processing log."""
    trace = ScanTrace()
    token = _current_trace.set(trace)
    try:
        info = extract_collector_info(image_bytes)
    finally:
        _current_trace.reset(token)
    trace.log()
    if SCAN_DEBUG_LOG:
        trace.write_to_file(SCAN_DEBUG_LOG)
    return info, trace


__all__ = [
    "ScanTrace",
    "isolate_card",
    "MAX_INPUT_BYTES",
    "init_ocr",
    "ocr_available",
    "extract_name",
    "extract_name_traced",
    "extract_collector_info",
    "extract_collector_info_traced",
    "get_isolated_preview",
]
