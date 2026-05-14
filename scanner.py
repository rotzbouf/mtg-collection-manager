"""
Card name extraction.

Primary:  EasyOCR  (handles MTG's stylised fonts much better than tesseract)
Fallback: pytesseract  (used if easyocr is not installed)

Pipeline per image:
  1. _open_image_safe  — decompression-bomb guard
  2. isolate_card      — remove surrounding background, keep the card
  3. _crop_name_zone   — crop to the title bar for OCR
"""

import asyncio
import io
import logging
import re
from typing import Optional

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

_MAX_IMAGE_PIXELS = 4096 * 4096  # ~67 MP — well above any real card photo
_CARD_ASPECT      = 7 / 5        # MTG card height ÷ width (3.5" ÷ 2.5")

logger = logging.getLogger(__name__)

# ── EasyOCR ──────────────────────────────────────────────────────────────────
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
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.set_device(0)
                gpu_arg = "cuda:0"
            else:
                gpu_arg = False
        except ImportError:
            gpu_arg = False
        _easyocr_reader = _easyocr_mod.Reader(
            ["de", "en"], gpu=gpu_arg, verbose=False
        )
        logger.info("EasyOCR ready (gpu=%s)", gpu_arg)

    await asyncio.to_thread(_load)


def ocr_available() -> bool:
    return _easyocr_available or _tesseract_available


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as [top-left, top-right, bottom-right, bottom-left]."""
    s    = pts.sum(axis=1)
    diff = pts[:, 1] - pts[:, 0]
    return np.array([
        pts[np.argmin(s)],    # top-left     (smallest x+y)
        pts[np.argmin(diff)], # top-right    (smallest y-x)
        pts[np.argmax(s)],    # bottom-right (largest  x+y)
        pts[np.argmax(diff)], # bottom-left  (largest  y-x)
    ], dtype=np.float32)


def _is_card_shaped(pts: np.ndarray) -> bool:
    """Return True if the 4 ordered points describe a plausible MTG card rectangle."""
    tl, tr, br, bl = pts
    w = float(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = float(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if w <= 0 or h <= 0:
        return False
    ratio = w / h   # width-over-height; card is portrait so ratio < 1
    return 0.45 < ratio < 0.98


def _warp_quad(img_bgr: np.ndarray, quad: np.ndarray):
    """Perspective-warp img_bgr so that quad maps to a rectangle."""
    import cv2
    ordered = _order_quad(quad)
    tl, tr, br, bl = ordered
    out_w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    out_h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([
        [0, 0], [out_w - 1, 0],
        [out_w - 1, out_h - 1], [0, out_h - 1],
    ], dtype=np.float32)
    M = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(img_bgr, M, (out_w, out_h))


def _best_quad_in_contours(
    contours, min_area: float, retr_label: str
) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Walk contours (largest first) and return the first 4-point polygon that
    passes the card-shape test, or None.  Also returns the best minAreaRect
    candidate as a second value so the caller can use it as a fallback.
    """
    import cv2

    _EPSILONS = (0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10)
    large = sorted(
        [c for c in contours if cv2.contourArea(c) >= min_area],
        key=cv2.contourArea, reverse=True,
    )
    best_rect = None
    for cnt in large[:10]:
        peri = cv2.arcLength(cnt, True)
        for eps in _EPSILONS:
            approx = cv2.approxPolyDP(cnt, eps * peri, True)
            if len(approx) == 4:
                pts = approx.reshape(4, 2).astype(np.float32)
                if _is_card_shaped(_order_quad(pts)):
                    logger.info(
                        "CV card detection: approxPolyDP (%s) eps=%.2f area=%.0f",
                        retr_label, eps, cv2.contourArea(cnt),
                    )
                    return pts, best_rect
        if best_rect is None:
            rect = cv2.minAreaRect(cnt)
            box = cv2.boxPoints(rect).astype(np.float32)
            if _is_card_shaped(_order_quad(box)):
                best_rect = box
    return None, best_rect


def _isolate_card_cv(img: Image.Image) -> Optional[Image.Image]:
    """
    Detect the card outline and perspective-correct it.

    Pass 1 — Otsu threshold (fast, reliable for any uniform background):
      Binarise the image; the card becomes the largest blob.

    Pass 2 — Canny edges (works when background ≈ card brightness):
      Multiple blur / threshold / retrieval-mode combinations.

    In both passes we first try approxPolyDP; if no clean quad is found we
    keep the best minAreaRect candidate as a last-resort fallback.
    """
    import cv2

    img_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    h, w   = img_bgr.shape[:2]
    min_area = 0.05 * h * w   # card must cover ≥ 5 % of the image

    gray    = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    quad: Optional[np.ndarray]      = None
    best_rect: Optional[np.ndarray] = None

    # ── Pass 1: Otsu threshold ────────────────────────────────────────────────
    # Try both polarities so we handle dark-on-light AND light-on-dark cards.
    for flags in (
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        cv2.THRESH_BINARY     + cv2.THRESH_OTSU,
    ):
        _, thresh = cv2.threshold(blurred, 0, 255, flags)
        # Close small holes (e.g. white mana symbols on a dark card)
        thresh = cv2.morphologyEx(
            thresh, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=3
        )
        for retr, label in (
            (cv2.RETR_EXTERNAL, "otsu-ext"),
            (cv2.RETR_LIST,     "otsu-list"),
        ):
            contours, _ = cv2.findContours(thresh, retr, cv2.CHAIN_APPROX_SIMPLE)
            q, r = _best_quad_in_contours(contours, min_area, label)
            if q is not None:
                quad = q
                break
            if r is not None and best_rect is None:
                best_rect = r
        if quad is not None:
            break

    # ── Pass 2: Canny edges ───────────────────────────────────────────────────
    if quad is None:
        for blur_k in (3, 5, 9, 13):
            blurred_k = cv2.GaussianBlur(gray, (blur_k, blur_k), 0)
            for lo, hi in ((20, 60), (30, 90), (50, 150), (80, 200), (100, 250)):
                edges = cv2.Canny(blurred_k, lo, hi)
                edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
                for retr, label in (
                    (cv2.RETR_EXTERNAL, f"canny-ext blur={blur_k} {lo}/{hi}"),
                    (cv2.RETR_LIST,     f"canny-list blur={blur_k} {lo}/{hi}"),
                ):
                    contours, _ = cv2.findContours(edges, retr, cv2.CHAIN_APPROX_SIMPLE)
                    q, r = _best_quad_in_contours(contours, min_area, label)
                    if q is not None:
                        quad = q
                        break
                    if r is not None and best_rect is None:
                        best_rect = r
                if quad is not None:
                    break
            if quad is not None:
                break

    # ── Warp ─────────────────────────────────────────────────────────────────
    chosen = quad if quad is not None else best_rect
    if chosen is None:
        logger.info("CV card detection: no card-shaped quad found (all passes exhausted)")
        return None

    method = "approxPolyDP" if quad is not None else "minAreaRect-fallback"
    try:
        warped = _warp_quad(img_bgr, chosen)
    except Exception as e:
        logger.info("CV card detection: warp failed (%s)", e)
        return None

    logger.info("Card isolated via CV %s → %dx%d", method, warped.shape[1], warped.shape[0])
    return Image.fromarray(cv2.cvtColor(warped, cv2.COLOR_BGR2RGB))


def _isolate_card_centre(img: Image.Image) -> Image.Image:
    """Last-resort fallback: centre crop at MTG card aspect ratio (5:7)."""
    w, h = img.size
    if h / w > _CARD_ASPECT:
        new_h = int(w * _CARD_ASPECT)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))
    new_w = int(h / _CARD_ASPECT)
    left = (w - new_w) // 2
    return img.crop((left, 0, left + new_w, h))


def isolate_card(img: Image.Image) -> Image.Image:
    """
    Isolate the MTG card from its background.

    1. OpenCV edge detection + perspective warp  (works on any background)
    2. Centre crop at 5:7 aspect ratio           (fallback if CV fails)
    """
    try:
        result = _isolate_card_cv(img)
        if result is not None:
            return result
        logger.debug("CV card detection found no quad — using centre crop")
    except Exception as e:
        logger.warning("CV card isolation failed (%s) — using centre crop", e)
    return _isolate_card_centre(img)


_NAME_LEFT   = 0.04
_NAME_TOP    = 0.03
_NAME_RIGHT  = 0.80   # wider: captures full name incl. long German titles
_NAME_BOTTOM = 0.10   # narrower: excludes the top of the art zone

_FOOTER_TOP    = 0.89  # bottom strip: copyright / set / collector / language
_FOOTER_BOTTOM = 0.97

# Language codes printed on MTG cards → Scryfall language identifiers
_CARD_LANG_MAP: dict[str, str] = {
    "EN": "en", "DE": "de", "FR": "fr", "IT": "it",
    "ES": "es", "PT": "pt", "JA": "ja", "JP": "ja",
    "KO": "ko", "RU": "ru", "ZHS": "zhs", "ZHT": "zht",
    "CS": "zhs", "CT": "zht",   # older print abbreviations for Chinese
}
_FOOTER_LANG_RE = re.compile(r'\b(EN|DE|FR|IT|ES|PT|JA|JP|KO|RU|ZHS|ZHT|CS|CT)\b')
_FOOTER_COLL_RE = re.compile(r'\b(\d{1,4})\s*/\s*\d{1,4}\b')


def _crop_name_zone(img: Image.Image) -> Image.Image:
    """Crop and enhance the card-name area for better OCR."""
    w, h = img.size
    zone = img.crop((
        int(w * _NAME_LEFT), int(h * _NAME_TOP),
        int(w * _NAME_RIGHT), int(h * _NAME_BOTTOM),
    ))
    zone = zone.resize((zone.width * 3, zone.height * 3), Image.LANCZOS)

    # CLAHE on the L-channel of LAB color space: boosts local contrast while
    # preserving hue, handling variable metallic/colored name bars on MTG cards.
    # Global contrast enhancement (the previous approach) can clip highlights on
    # light name bars and crush shadows on dark ones — CLAHE avoids both.
    try:
        import cv2
        lab = cv2.cvtColor(np.array(zone), cv2.COLOR_RGB2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
        lab = cv2.merge([clahe.apply(l_ch), a_ch, b_ch])
        zone = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    except Exception:
        zone = ImageEnhance.Contrast(zone).enhance(2.5)

    return zone.filter(ImageFilter.SHARPEN)


def _crop_footer_zone(img: Image.Image) -> Image.Image:
    """Crop and preprocess the card-footer strip for set/collector/language OCR."""
    w, h = img.size
    zone = img.crop((int(w * 0.02), int(h * _FOOTER_TOP), int(w * 0.98), int(h * _FOOTER_BOTTOM)))
    # 5× upscale — footer text is much smaller than the card name
    zone = zone.resize((zone.width * 5, zone.height * 5), Image.LANCZOS)
    # Grayscale + CLAHE: footer text is black-on-white/cream, so grayscale is fine;
    # CLAHE handles any shadow or uneven illumination from the photo.
    try:
        import cv2
        gray = cv2.cvtColor(np.array(zone), cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        zone = Image.fromarray(clahe.apply(gray))
    except Exception:
        zone = zone.convert("L")
        zone = ImageEnhance.Contrast(zone).enhance(2.0)
    return zone


def _parse_footer(text: str) -> dict:
    """
    Extract set_code, collector_number, language from raw OCR footer text.

    Typical footer line: "™ & © 2022 Wizards of the Coast LLC NEO · DE 042/270"
    All returned values may be None if the OCR text is too noisy.
    """
    upper = text.upper()

    # Language code (2–3 uppercase letters explicitly printed on every modern card)
    lang_m = _FOOTER_LANG_RE.search(upper)
    language = _CARD_LANG_MAP.get(lang_m.group(1)) if lang_m else None

    # Collector number: prefer "X/Y" format over a bare number
    coll_m = _FOOTER_COLL_RE.search(upper)
    collector_number = coll_m.group(1).lstrip("0") or "0" if coll_m else None

    # Set code: last 2–5-char alphanumeric word just before the language code.
    # In "... LLC NEO · DE 042/270" → set_code = "NEO".
    set_code = None
    if lang_m:
        before_lang = upper[:lang_m.start()].rstrip(" ·-—·")
        for token in reversed(before_lang.split()):
            token = re.sub(r"[^A-Z0-9]", "", token)
            if 2 <= len(token) <= 5 and token not in {
                "LLC", "INC", "LTD", "THE", "AND", "FOR", "ART",
                "COAST", "WIZARDS", "HASBRO",
            }:
                set_code = token
                break

    return {"set_code": set_code, "collector_number": collector_number, "language": language}


def extract_collector_info(image_bytes: bytes) -> dict:
    """
    OCR the card footer to extract set code, collector number, and card language.

    Returns a dict with keys (all may be None):
      set_code         — Scryfall set code, e.g. "NEO", "M21"
      collector_number — collector number string, e.g. "42"
      language         — Scryfall language code, e.g. "de", "en"

    Primary engine: EasyOCR.  Fallback: Tesseract (--psm 6).
    """
    try:
        img = _open_image_safe(image_bytes)
        if img is None:
            return {}
        card = isolate_card(img)
        card = _ensure_min_width(card)
        zone = _crop_footer_zone(card)

        # EasyOCR path — use a lower confidence floor than the name zone because
        # footer text is inherently smaller and noisier in photos.
        if _easyocr_reader:
            results = _easyocr_reader.readtext(np.array(zone), detail=1, paragraph=False)
            logger.info("Footer EasyOCR raw: %s", [(r[1], round(r[2], 2)) for r in results])
            text = " ".join(r[1] for r in results if r[2] >= 0.15)
            info = _parse_footer(text)
            if info.get("collector_number") or info.get("language"):
                logger.info("Footer parsed (EasyOCR): %s", info)
                return info

        # Tesseract fallback
        if _tesseract_available:
            raw = _pytesseract.image_to_string(zone, config="--psm 6 --oem 3").strip()
            info = _parse_footer(raw)
            logger.info("Footer parsed (Tesseract): %s", info)
            return info

    except Exception as e:
        logger.error("extract_collector_info error: %s", e)

    return {}


def _open_image_safe(image_bytes: bytes) -> Optional[Image.Image]:
    """Open an image while rejecting decompression bombs."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()  # catches truncated / malformed headers early
        img = Image.open(io.BytesIO(image_bytes))  # re-open after verify (verify() closes)
        if (img.width * img.height) > _MAX_IMAGE_PIXELS:
            logger.warning("Image rejected: %dx%d exceeds pixel limit", img.width, img.height)
            return None
        return img.convert("RGB")
    except Exception as e:
        logger.error("Failed to open image: %s", e)
        return None


def _ensure_min_width(img: Image.Image, min_px: int = 500) -> Image.Image:
    """Upscale a too-small card so OCR has enough pixels to work with."""
    if img.width < min_px:
        scale = min_px / img.width
        img = img.resize((min_px, int(img.height * scale)), Image.LANCZOS)
    return img


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
        logger.info("EasyOCR raw: %s", [(r[1], round(r[2], 2)) for r in results])
        if not results:
            return None
        # Collect all segments above the confidence floor, ordered left-to-right.
        # This handles multi-word names that EasyOCR splits into separate boxes.
        MIN_CONF = 0.45
        confident = sorted(
            [r for r in results if r[2] >= MIN_CONF],
            key=lambda r: r[0][0][0],  # sort by left-x of bounding box
        )
        text = (
            " ".join(r[1].strip() for r in confident).strip()
            if confident
            else max(results, key=lambda r: r[2])[1].strip()
        )
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
        text = " ".join(raw.split()).replace("|", "l").replace("0", "O")
        return text if len(text) > 1 else None
    except Exception as e:
        logger.error("Tesseract error: %s", e)
        return None


def extract_name(image_bytes: bytes) -> Optional[str]:
    """Try EasyOCR first, fall back to tesseract."""
    return _easyocr_extract(image_bytes) or _tesseract_extract(image_bytes)


def get_isolated_preview(image_bytes: bytes) -> Optional[bytes]:
    """
    Return the card after isolation + name-zone highlight as JPEG bytes.
    Used only when DEBUG_SCAN_PREVIEW=1 — not called in production.
    The returned image shows:
      • full isolated card (what goes into phash)
      • red rectangle overlaid on the OCR name zone
    """
    try:
        from PIL import ImageDraw
        img = _open_image_safe(image_bytes)
        if img is None:
            return None
        card = isolate_card(img)

        # Draw the OCR name zone as a red rectangle so it's visible
        w, h = card.size
        draw = ImageDraw.Draw(card)
        x0, y0 = int(w * _NAME_LEFT),  int(h * _NAME_TOP)
        x1, y1 = int(w * _NAME_RIGHT), int(h * _NAME_BOTTOM)
        draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=max(2, w // 150))

        buf = io.BytesIO()
        card.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.warning("get_isolated_preview failed: %s", e)
        return None
