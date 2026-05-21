"""Scan debug tracing — collects processing steps and OCR confidence per scan.

Usage
-----
    from core.scanner.debug import ScanTrace, _current_trace

    trace = ScanTrace()
    token = _current_trace.set(trace)
    try:
        name = extract_name(image_bytes)
    finally:
        _current_trace.reset(token)
    trace.log()

All internal scanner functions call the module-level helpers _step() / _trace()
which are no-ops when no trace is active, so there is zero overhead in normal use.

Environment variable SCAN_DEBUG_LOG=<path> activates file logging: a JSON-lines
record is appended after every traced scan.
"""
from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SCAN_DEBUG_LOG: Optional[str] = os.environ.get("SCAN_DEBUG_LOG")


@dataclass
class ScanTrace:
    """Accumulated debug information for a single scan attempt."""

    steps: list[str] = field(default_factory=list)
    """Ordered processing steps taken, e.g. 'isolation: cv-otsu pass1', 'easyocr: 3 segs'."""

    engine: str = "none"
    """OCR engine used: 'easyocr' | 'tesseract' | 'none'."""

    name_segments: list[tuple[str, float]] = field(default_factory=list)
    """All OCR segments detected in the name zone: [(text, confidence), ...]."""

    name_confidence: float = 0.0
    """Highest single-segment confidence for the extracted name."""

    footer_segments: list[tuple[str, float]] = field(default_factory=list)
    """All OCR segments detected in the footer zone."""

    parsed_footer: dict = field(default_factory=dict)
    """Output of _parse_footer(): set_code, collector_number, language."""

    extracted_name: Optional[str] = None
    """Final extracted name string after normalisation."""

    # ------------------------------------------------------------------ #

    def summary(self) -> str:
        """One-line summary suitable for log messages or UI display."""
        parts = [f"engine={self.engine}"]
        if self.name_confidence:
            parts.append(f"conf={self.name_confidence:.2f}")
        if self.extracted_name:
            parts.append(f"name={self.extracted_name!r}")
        if self.parsed_footer:
            pf = self.parsed_footer
            footer_str = "/".join(
                str(v) for v in [pf.get("set_code"), pf.get("collector_number"), pf.get("language")]
                if v
            )
            if footer_str:
                parts.append(f"footer={footer_str}")
        return "  ".join(parts)

    def log(self) -> None:
        """Log the full trace at DEBUG level."""
        logger.debug("=== Scan trace: %s ===", self.summary())
        for s in self.steps:
            logger.debug("  · %s", s)
        if self.name_segments:
            logger.debug(
                "  OCR name segments: %s",
                [(t, round(c, 2)) for t, c in self.name_segments],
            )
        if self.footer_segments:
            logger.debug(
                "  OCR footer segments: %s",
                [(t, round(c, 2)) for t, c in self.footer_segments],
            )

    def write_to_file(self, path: str) -> None:
        """Append a JSON record to *path* (creates file if missing)."""
        record = {
            "ts":               datetime.now(timezone.utc).isoformat(),
            "engine":           self.engine,
            "name_confidence":  self.name_confidence,
            "extracted_name":   self.extracted_name,
            "parsed_footer":    self.parsed_footer,
            "steps":            self.steps,
            "name_segments":    self.name_segments,
            "footer_segments":  self.footer_segments,
        }
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("Could not write scan debug log to %r: %s", path, exc)


# ── Context-variable helpers ───────────────────────────────────────────────────

_current_trace: ContextVar[Optional[ScanTrace]] = ContextVar(
    "_current_trace", default=None
)


def _trace() -> Optional[ScanTrace]:
    """Return the active ScanTrace for this call stack, or None."""
    return _current_trace.get()


def _step(msg: str) -> None:
    """Append *msg* to the active trace's steps list (no-op if no trace active)."""
    t = _current_trace.get()
    if t is not None:
        t.steps.append(msg)
