"""High-level extraction pipeline — orchestrates isolation + OCR."""
from __future__ import annotations

import io
import logging

import numpy as np

from .isolation import isolate_card, _open_image_safe, _ensure_min_width, MAX_INPUT_BYTES
from . import ocr as _ocr
from .debug import _step, _trace

logger = logging.getLogger(__name__)


def extract_collector_info(image_bytes: bytes) -> dict:
    """
    OCR the card footer to extract set code, collector number, and card language.

    Returns a dict with keys (all may be None):
      set_code         — Scryfall set code, e.g. "NEO", "M21"
      collector_number — collector number string, e.g. "42"
      language         — Scryfall language code, e.g. "de", "en"

    Primary engine: EasyOCR.  Fallback: Tesseract (--psm 6).
    """
    if len(image_bytes) > MAX_INPUT_BYTES:
        logger.warning(
            "extract_collector_info: image too large (%d bytes), skipping",
            len(image_bytes),
        )
        return {}
    try:
        img = _open_image_safe(image_bytes)
        if img is None:
            return {}
        card = isolate_card(img)
        card = _ensure_min_width(card)
        zone = _ocr._crop_footer_zone(card)

        # EasyOCR path — lower confidence floor than name zone because footer
        # text is inherently smaller and noisier in photos.
        reader = _ocr._easyocr_reader
        if reader:
            results = reader.readtext(np.array(zone), detail=1, paragraph=False)
            logger.debug("Footer EasyOCR raw: %s", [(r[1], round(r[2], 2)) for r in results])
            t = _trace()
            if t is not None:
                t.footer_segments = [(r[1], round(r[2], 3)) for r in results]
            _step(
                f"footer-easyocr: {len(results)} segment(s), "
                f"best_conf={max((r[2] for r in results), default=0.0):.2f}"
            )
            text = "\n".join(r[1] for r in results if r[2] >= 0.15)
            info = _ocr._parse_footer(text)
            if t is not None:
                t.parsed_footer = info
            _step(f"footer-parsed: {info}")
            if info.get("collector_number") or info.get("language"):
                logger.debug("Footer parsed (EasyOCR): %s", info)
                return info

        # Tesseract fallback
        if _ocr._tesseract_available:
            import pytesseract as _pytesseract
            raw = _pytesseract.image_to_string(zone, config="--psm 6 --oem 3").strip()
            _step(f"footer-tesseract: raw={raw!r}")
            info = _ocr._parse_footer(raw)
            t = _trace()
            if t is not None:
                t.parsed_footer = info
            _step(f"footer-parsed: {info}")
            logger.debug("Footer parsed (Tesseract): %s", info)
            return info

    except Exception as e:
        logger.error("extract_collector_info error: %s", e)

    return {}


def get_isolated_preview(image_bytes: bytes) -> bytes | None:
    """
    Return the isolated card with OCR zone highlights as JPEG bytes.
    Only used when DEBUG_SCAN_PREVIEW=1 — not called in production.

    Overlays:
      red rectangle  — name zone
      blue rectangle — footer zone (set code / collector number / language)
    """
    try:
        from PIL import ImageDraw
        img = _open_image_safe(image_bytes)
        if img is None:
            return None
        card = isolate_card(img)

        w, h = card.size
        lw = max(2, w // 150)
        draw = ImageDraw.Draw(card)

        draw.rectangle(
            [int(w * _ocr._NAME_LEFT),   int(h * _ocr._NAME_TOP),
             int(w * _ocr._NAME_RIGHT),  int(h * _ocr._NAME_BOTTOM)],
            outline=(255, 0, 0), width=lw,
        )
        draw.rectangle(
            [int(w * _ocr._FOOTER_LEFT),   int(h * _ocr._FOOTER_TOP),
             int(w * _ocr._FOOTER_RIGHT),  int(h * _ocr._FOOTER_BOTTOM)],
            outline=(0, 100, 255), width=lw,
        )

        buf = io.BytesIO()
        card.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.warning("get_isolated_preview failed: %s", e)
        return None
