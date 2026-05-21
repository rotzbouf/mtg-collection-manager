"""OCR engines (EasyOCR / Tesseract), zone preprocessing, and text extraction."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance

from .isolation import isolate_card, _open_image_safe, _ensure_min_width, MAX_INPUT_BYTES

logger = logging.getLogger(__name__)

# ── EasyOCR ───────────────────────────────────────────────────────────────────
_easyocr_reader = None
_easyocr_available = False

try:
    import easyocr as _easyocr_mod
    _easyocr_available = True
except ImportError:
    logger.warning("easyocr not installed — falling back to tesseract")

# ── Tesseract (fallback) ──────────────────────────────────────────────────────
_tesseract_available = False
try:
    import pytesseract as _pytesseract
    _tesseract_available = True
except ImportError:
    pass


async def init_ocr():
    """Pre-load EasyOCR model (slow first time — call once at startup)."""
    global _easyocr_reader
    if not _easyocr_available:
        return

    def _load():
        global _easyocr_reader
        _easyocr_reader = _easyocr_mod.Reader(
            ["de", "en"], gpu=False, verbose=False
        )
        logger.info("EasyOCR ready (cpu)")

    await asyncio.to_thread(_load)


def ocr_available() -> bool:
    return _easyocr_available or _tesseract_available


# ── Zone crop constants ───────────────────────────────────────────────────────

_NAME_LEFT   = 0.04
_NAME_TOP    = 0.03
_NAME_RIGHT  = 0.80   # wider: captures full name incl. long German titles
_NAME_BOTTOM = 0.10   # narrower: excludes the top of the art zone

_FOOTER_TOP    = 0.93  # bottom-left corner: set code / collector number / language
_FOOTER_BOTTOM = 0.99
_FOOTER_LEFT   = 0.04
_FOOTER_RIGHT  = 0.23

# ── Footer parsing constants ──────────────────────────────────────────────────

# Language codes printed on MTG cards → Scryfall language identifiers
_CARD_LANG_MAP: dict[str, str] = {
    "EN": "en", "DE": "de", "FR": "fr", "IT": "it",
    "ES": "es", "PT": "pt", "JA": "ja", "JP": "ja",
    "KO": "ko", "RU": "ru", "ZHS": "zhs", "ZHT": "zht",
    "CS": "zhs", "CT": "zht",   # older print abbreviations for Chinese
}
_FOOTER_LANG_RE = re.compile(r'\b(EN|DE|FR|IT|ES|PT|JA|JP|KO|RU|ZHS|ZHT|CS|CT)\b')
_FOOTER_COLL_RE = re.compile(r'\b(\d{1,4})\s*/\s*\d{1,4}\b')


# ── Zone preprocessing ────────────────────────────────────────────────────────

def _crop_name_zone(img: Image.Image) -> Image.Image:
    """Crop and enhance the card-name area for better OCR."""
    w, h = img.size
    zone = img.crop((
        int(w * _NAME_LEFT), int(h * _NAME_TOP),
        int(w * _NAME_RIGHT), int(h * _NAME_BOTTOM),
    ))
    zone = zone.resize((zone.width * 3, zone.height * 3), Image.LANCZOS)

    try:
        import cv2
        arr = np.array(zone)
        # Gentle denoise before CLAHE: a 3×3 Gaussian removes sensor/JPEG noise
        # without blurring character edges, preventing CLAHE from amplifying it.
        arr = cv2.GaussianBlur(arr, (3, 3), 0)
        # CLAHE on L-channel: boosts local contrast while preserving hue.
        # clipLimit 2.0 and finer 8×8 tile grid reduce over-amplification
        # on the highly varied metallic/coloured name bars MTG cards use.
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab = cv2.merge([clahe.apply(l_ch), a_ch, b_ch])
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        # Unsharp mask: blend enhanced with a blurred copy to sharpen edges without
        # the ringing artefacts PIL's static SHARPEN kernel introduces.
        blurred = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
        zone = Image.fromarray(cv2.addWeighted(enhanced, 1.4, blurred, -0.4, 0))
    except Exception:
        zone = ImageEnhance.Contrast(zone).enhance(2.5)

    return zone


def _crop_footer_zone(img: Image.Image) -> Image.Image:
    """Crop and preprocess the card-footer strip for set/collector/language OCR."""
    w, h = img.size
    zone = img.crop((
        int(w * _FOOTER_LEFT), int(h * _FOOTER_TOP),
        int(w * _FOOTER_RIGHT), int(h * _FOOTER_BOTTOM),
    ))
    # 5× upscale — footer text is much smaller than the card name
    zone = zone.resize((zone.width * 5, zone.height * 5), Image.LANCZOS)
    try:
        import cv2
        gray = cv2.cvtColor(np.array(zone), cv2.COLOR_RGB2GRAY)
        # NLM denoising before binarization: removes photo/JPEG noise that would
        # otherwise survive thresholding as isolated black specks.
        gray = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)
        # CLAHE equalises illumination across the strip (shadows from hand-held photos).
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        # Adaptive binarization: local thresholds handle the cream-coloured card stock
        # and any residual illumination gradient across the narrow footer strip.
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            blockSize=15, C=4,
        )
        # Morphological opening: removes isolated noise pixels smaller than a text
        # stroke without touching the strokes themselves.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        zone = Image.fromarray(cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel))
    except Exception:
        zone = zone.convert("L")
        zone = ImageEnhance.Contrast(zone).enhance(2.0)
    return zone


# ── Text parsing ──────────────────────────────────────────────────────────────

def _parse_footer(text: str) -> dict:
    """
    Extract set_code, collector_number, language from raw OCR footer text.

    The blue box covers two printed lines in the card's bottom-left corner:
      Line 1:  collector number   e.g. "042/270"  (or just "042")
      Line 2:  set code · lang    e.g. "NEO · DE"

    All returned values may be None if the OCR text is too noisy.
    """
    _BLOCKLIST = {"LLC", "INC", "LTD", "THE", "AND", "FOR", "ART",
                  "COAST", "WIZARDS", "HASBRO"}
    upper = text.upper()
    lines = [ln.strip() for ln in upper.splitlines() if ln.strip()]

    # ── Collector number ──────────────────────────────────────────────────────
    _BARE_NUM_RE = re.compile(r'^\D{0,3}(\d{1,4})\D{0,3}$')
    collector_number = None
    coll_m = _FOOTER_COLL_RE.search(upper)
    if coll_m:
        collector_number = coll_m.group(1).lstrip("0") or None
    else:
        for line in lines:
            bare_m = _BARE_NUM_RE.match(line)
            if bare_m:
                collector_number = bare_m.group(1).lstrip("0") or None
                break
        # Last resort: number at the very start of the full text (merged-line OCR)
        if not collector_number:
            start_m = re.match(r'^(\d{1,4})\b', upper.lstrip())
            if start_m:
                collector_number = start_m.group(1).lstrip("0") or None

    # ── Language code ─────────────────────────────────────────────────────────
    lang_m = _FOOTER_LANG_RE.search(upper)
    language = _CARD_LANG_MAP.get(lang_m.group(1)) if lang_m else None

    # ── Set code: first valid token on the line that contains the language code
    def _valid_set_token(t: str) -> bool:
        return 2 <= len(t) <= 5 and t not in _BLOCKLIST and any(c.isalpha() for c in t)

    set_code = None
    for line in lines:
        lm = _FOOTER_LANG_RE.search(line)
        if lm:
            for token in line[:lm.start()].split():
                token = re.sub(r"[^A-Z0-9]", "", token)
                if _valid_set_token(token):
                    set_code = token
                    break
            break

    # Fallback: last valid token before the lang code anywhere in the full text
    if not set_code and lang_m:
        before_lang = upper[:lang_m.start()].rstrip(" ·-—·")
        for token in reversed(before_lang.split()):
            token = re.sub(r"[^A-Z0-9]", "", token)
            if _valid_set_token(token):
                set_code = token
                break

    return {"set_code": set_code, "collector_number": collector_number, "language": language}


def _normalize_ocr(text: str) -> str:
    """Shared post-processing for raw OCR text from any engine."""
    text = " ".join(text.split())
    text = (
        text.replace("|",  "l")   # pipe       → l  (vertical bar misread as l)
            .replace("¦",  "l")   # broken bar → l
            .replace("0",  "O")   # zero       → O  (card names never use digit 0)
            .replace("vv", "w")   # double-v   → w  (camera lens OCR artefact)
    )
    text = text.strip("■•·–—~`^°*@#")
    return text


# ── Per-engine extraction ─────────────────────────────────────────────────────

def _easyocr_extract(image_bytes: bytes) -> Optional[str]:
    if not _easyocr_reader:
        return None
    try:
        img = _open_image_safe(image_bytes)
        if img is None:
            return None
        img = isolate_card(img)
        img = _ensure_min_width(img)
        zone = _crop_name_zone(img)
        results = _easyocr_reader.readtext(np.array(zone), detail=1, paragraph=False)
        logger.debug("EasyOCR raw: %s", [(r[1], round(r[2], 2)) for r in results])
        if not results:
            return None
        # Collect all segments above the confidence floor, ordered left-to-right.
        MIN_CONF = 0.45
        confident = sorted(
            [r for r in results if r[2] >= MIN_CONF],
            key=lambda r: r[0][0][0],  # sort by left-x of bounding box
        )
        raw = (
            " ".join(r[1].strip() for r in confident).strip()
            if confident
            else max(results, key=lambda r: r[2])[1].strip()
        )
        text = _normalize_ocr(raw)
        return text if len(text) > 1 else None
    except Exception as e:
        logger.error("EasyOCR error: %s", e)
        return None


def _tesseract_extract(image_bytes: bytes) -> Optional[str]:
    if not _tesseract_available:
        return None
    try:
        img = _open_image_safe(image_bytes)
        if img is None:
            return None
        img = isolate_card(img)
        img = _ensure_min_width(img)
        zone = _crop_name_zone(img).convert("L")
        raw = _pytesseract.image_to_string(zone, config="--psm 7 --oem 3").strip()
        text = _normalize_ocr(raw)
        return text if len(text) > 1 else None
    except Exception as e:
        logger.error("Tesseract error: %s", e)
        return None


# ── Public extraction API ─────────────────────────────────────────────────────

def extract_name(image_bytes: bytes) -> Optional[str]:
    """Try EasyOCR first, fall back to tesseract."""
    if len(image_bytes) > MAX_INPUT_BYTES:
        logger.warning("extract_name: image too large (%d bytes), skipping", len(image_bytes))
        return None
    return _easyocr_extract(image_bytes) or _tesseract_extract(image_bytes)
