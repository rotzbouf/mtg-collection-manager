"""Shared OCR → Scryfall card resolution pipeline.

Used by both the Discord bot (cogs/scan.py) and the desktop scanner widget
so the resolution logic lives in exactly one place.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
from typing import TYPE_CHECKING, Optional

from core.sanitize import sanitize_text

if TYPE_CHECKING:
    from core.scryfall import ScryfallClient

logger = logging.getLogger(__name__)

# Minimum fuzzy-match ratio to call a name "confirmed" on top of a collector hit
_NAME_CONFIRM_RATIO = 0.55


def _run_ocr_sync(image_bytes: bytes) -> tuple[Optional[str], dict]:
    """Run name OCR and footer OCR sequentially in a worker thread.

    EasyOCR is not thread-safe for concurrent calls, so both extractions
    are serialised inside the same thread invocation.
    """
    from core import scanner as sc

    extracted_name = sc.extract_name(image_bytes)
    collector_info = sc.extract_collector_info(image_bytes) or {}
    return extracted_name, collector_info


def _fuzzy_ratio(a: str, b: str) -> float:
    """Case-fold and normalise both strings before sequence matching."""
    a_n = sanitize_text(a, max_len=200).lower()
    b_n = sanitize_text(b, max_len=200).lower()
    return difflib.SequenceMatcher(None, a_n, b_n).ratio()


async def resolve_scan(
    scryfall: "ScryfallClient",
    image_bytes: bytes,
) -> tuple[Optional[dict], str, list[str], Optional[str], dict]:
    """Full scan pipeline: OCR then Scryfall lookup.

    Returns a 5-tuple:
        card           — resolved Scryfall card dict, or None on failure
        detected_lang  — language code ('en', 'de', …)
        method_parts   — human-readable list of how the match was made
        extracted_name — raw OCR name string (may be None)
        collector_info — dict with set_code, collector_number, language keys
    """
    extracted_name, collector_info = await asyncio.to_thread(
        _run_ocr_sync, image_bytes
    )
    logger.debug("OCR name: %r  footer: %s", extracted_name, collector_info)

    # ── Collector match (exact set + number → Scryfall) ───────────────────
    collector_card: Optional[dict] = None
    if collector_info.get("set_code") and collector_info.get("collector_number"):
        clang = collector_info.get("language") or "en"
        collector_card = await scryfall.get_by_collector(
            collector_info["set_code"], collector_info["collector_number"], clang
        )
        if not collector_card and clang != "en":
            collector_card = await scryfall.get_by_collector(
                collector_info["set_code"], collector_info["collector_number"], "en"
            )

    # ── OCR name match (only when collector lookup failed) ────────────────
    ocr_card: Optional[dict] = None
    ocr_lang = "unknown"
    if extracted_name and not collector_card:
        set_hint = collector_info.get("set_code")
        ocr_card, ocr_lang = await scryfall.resolve_card(
            extracted_name, set_code=set_hint
        )
        if not ocr_card and set_hint:
            # Retry without the set hint — OCR may have misread the set code
            ocr_card, ocr_lang = await scryfall.resolve_card(extracted_name)

    footer_lang = collector_info.get("language")
    method_parts: list[str] = []

    if collector_card:
        detected_lang = footer_lang or "en"
        set_info = (
            f'{collector_info["set_code"]} #{collector_info["collector_number"]}'
        )
        method_parts.append(f"collector [{set_info}]")
        if extracted_name:
            en = collector_card.get("name_en", "")
            de = collector_card.get("name_de") or collector_card.get("printed_name", "")
            ratio = max(
                _fuzzy_ratio(extracted_name, en),
                _fuzzy_ratio(extracted_name, de) if de else 0.0,
            )
            if ratio >= _NAME_CONFIRM_RATIO:
                method_parts.append(
                    f'name confirmed: "{extracted_name}" ({ratio:.0%})'
                )
            else:
                logger.debug(
                    "Collector/name mismatch: OCR=%r vs %r (ratio=%.2f)",
                    extracted_name, en, ratio,
                )
                method_parts.append(
                    f'OCR: "{extracted_name}" (differs {ratio:.0%})'
                )
        return collector_card, detected_lang, method_parts, extracted_name, collector_info

    if ocr_card:
        detected_lang = footer_lang or (ocr_lang if ocr_lang != "unknown" else "en")
        method_parts.append(f'OCR [{ocr_lang}]: "{extracted_name}"')
        if footer_lang:
            method_parts.append(f"lang: {footer_lang} (footer)")
        return ocr_card, detected_lang, method_parts, extracted_name, collector_info

    return None, "en", [], extracted_name, collector_info


def no_match_message(extracted_name: Optional[str], collector_info: dict) -> str:
    """Human-readable reason why a scan produced no card match."""
    from core import scanner as sc

    if not sc.ocr_available():
        return "OCR not available. Use the manual name field or `/add` instead."
    if collector_info.get("set_code") and collector_info.get("collector_number"):
        return (
            f'Collector info read ({collector_info["set_code"]} '
            f'#{collector_info["collector_number"]}) but no Scryfall match. '
            f"Enter the name manually."
        )
    if extracted_name:
        return (
            f'Could not match **"{extracted_name}"** on Scryfall. '
            f"Enter the name manually."
        )
    return "Could not read the card. Enter the name manually."
