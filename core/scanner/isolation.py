"""Card isolation — OpenCV edge detection and perspective warp."""
from __future__ import annotations

import io
import logging
from typing import Optional

import numpy as np
from PIL import Image

_MAX_IMAGE_PIXELS = 4096 * 4096
_CARD_ASPECT      = 7 / 5
MAX_INPUT_BYTES   = 20 * 1024 * 1024

logger = logging.getLogger(__name__)


# ── Image I/O helpers ─────────────────────────────────────────────────────────

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
        scale = min(min_px / img.width, 4.0)  # cap at 4× to avoid OOM on large inputs
        new_w = int(img.width * scale)
        new_h = int(img.height * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    return img


# ── Geometry helpers ──────────────────────────────────────────────────────────

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


def _warp_quad(img_bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
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
    passes the card-shape test.  Also returns the best minAreaRect candidate
    as a fallback value.
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
                    logger.debug(
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


# ── Card isolation ────────────────────────────────────────────────────────────

def _isolate_card_cv(img: Image.Image) -> Optional[Image.Image]:
    """
    Detect the card outline and perspective-correct it.

    Pass 1 — Otsu threshold (fast, reliable for uniform backgrounds):
      Binarise the image; the card becomes the largest blob.

    Pass 2 — Canny on Gaussian blur (works when background ≈ card brightness):
      Multiple blur / threshold / retrieval-mode combinations.

    Pass 3 — Canny on bilateral filter (textured / coloured backgrounds):
      Bilateral filter smooths background texture while preserving the sharp
      card boundary so Canny produces a clean rectangle on busy backgrounds.

    In all passes we first try approxPolyDP; if no clean quad is found we
    keep the best minAreaRect candidate as a last-resort fallback.
    """
    import cv2

    img_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    h, w    = img_bgr.shape[:2]
    min_area = 0.05 * h * w   # card must cover ≥ 5 % of the image

    gray    = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    quad: Optional[np.ndarray]      = None
    best_rect: Optional[np.ndarray] = None

    # ── Pass 1: Otsu threshold ───────────────────────────────────────────────
    for flags in (
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        cv2.THRESH_BINARY     + cv2.THRESH_OTSU,
    ):
        _, thresh = cv2.threshold(blurred, 0, 255, flags)
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

    # ── Pass 2: Canny on Gaussian blur ───────────────────────────────────────
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

    # ── Pass 3: Canny on bilateral filter ────────────────────────────────────
    if quad is None:
        for d, sig in ((9, 75), (15, 100), (9, 150)):
            bilateral = cv2.bilateralFilter(gray, d, sig, sig)
            for lo, hi in ((20, 60), (30, 90), (50, 150)):
                edges = cv2.Canny(bilateral, lo, hi)
                edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
                for retr, label in (
                    (cv2.RETR_EXTERNAL, f"bilateral-ext d={d} sig={sig} {lo}/{hi}"),
                    (cv2.RETR_LIST,     f"bilateral-list d={d} sig={sig} {lo}/{hi}"),
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
        logger.debug("CV card detection: no card-shaped quad found (all passes exhausted)")
        return None

    method = "approxPolyDP" if quad is not None else "minAreaRect-fallback"
    try:
        warped = _warp_quad(img_bgr, chosen)
    except Exception as e:
        logger.debug("CV card detection: warp failed (%s)", e)
        return None

    logger.debug("Card isolated via CV %s → %dx%d", method, warped.shape[1], warped.shape[0])
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
