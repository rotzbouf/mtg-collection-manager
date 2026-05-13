"""
Visual card matching via perceptual image hashing.

Build flow:
  1. Download Scryfall default-cards bulk JSON (~100 MB, one printing per card)
  2. For each card, download its 'small' thumbnail (~5-10 KB)
  3. Compute phash (64-bit perceptual hash) and store in the local DB

Match flow:
  1. Compute phash of the user's photo
  2. Search the in-memory hash cache with vectorised Hamming distance
  3. If distance ≤ MATCH_THRESHOLD → confident match → fetch full card from Scryfall

GPU acceleration:
  When PyTorch + CUDA is available the DCT-based pHash is computed on GPU in
  batches (index build) or single-shot (query time).  Falls back transparently
  to imagehash (scipy DCT, CPU-only) when PyTorch is absent or CUDA is not found.
"""

import asyncio
import io
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import imagehash
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

BULK_ENDPOINT   = "https://api.scryfall.com/bulk-data/default-cards"
SETS_ENDPOINT   = "https://api.scryfall.com/sets"
MATCH_THRESHOLD = 20      # Hamming distance out of 64 bits
_CONCURRENCY    = 8       # parallel image downloads
_RATE_DELAY     = 0.8     # seconds held per semaphore slot → 8 workers / 0.8 s = 10 req/s max
_GPU_HASH_BATCH = 64      # images per GPU batch during index build

_PLAYABLE_SET_TYPES = {
    "expansion", "core", "masters", "draft_innovation",
    "commander", "funny", "starter", "box",
}


# ─────────────────────────────────────────────────────────────────────────────
# GPU / DCT pHash
# ─────────────────────────────────────────────────────────────────────────────

_GPU_DEVICE: Optional[str] = None   # "cuda:0", "cpu", or None (torch absent)
_DCT_MATRIX = None                  # torch.Tensor (32×32 float64), set by _init_gpu


def _init_gpu() -> None:
    """
    Pre-compute the 32×32 separable DCT basis matrix and move it to the best
    available device.  Called once at import time; safe to call again.

    The matrix implements the type-II DCT used by imagehash.phash:
        M[k, n] = 2 · cos(π · k · (2n+1) / (2N))
    so that M @ pixels @ M.T reproduces scipy.fftpack.dct applied along both axes.
    float64 is used to match scipy's precision.
    """
    global _GPU_DEVICE, _DCT_MATRIX
    try:
        import torch
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        N = 32
        k = torch.arange(N, dtype=torch.float64)
        n = torch.arange(N, dtype=torch.float64)
        _DCT_MATRIX = (
            2.0 * torch.cos(
                torch.pi * k.unsqueeze(1) * (2.0 * n.unsqueeze(0) + 1.0) / (2.0 * N)
            )
        ).to(device)
        _GPU_DEVICE = str(device)
        if device.type == "cuda":
            logger.info("GPU pHash ready — %s", torch.cuda.get_device_name(0))
        else:
            logger.info("GPU pHash: CUDA not available, using CPU torch DCT")
    except ImportError:
        logger.debug("PyTorch not found — pHash via imagehash (CPU)")


_init_gpu()


def _normalize_image(img: Image.Image) -> Image.Image:
    """
    Lighting-invariant normalisation: autocontrast + mild Gaussian blur.
    Applied to both the Scryfall thumbnail (index build) and the user's photo
    (query), so the comparison is symmetric regardless of exposure.
    """
    from PIL import ImageFilter, ImageOps
    return ImageOps.autocontrast(
        img.filter(ImageFilter.GaussianBlur(radius=1)), cutoff=2
    )


def _phash_images(
    images: list[Image.Image],
) -> list[tuple[Optional[str], Optional[str]]]:
    """
    Compute (phash, phash_norm) for a batch of PIL images.

    phash      — hash of the raw image (matches imagehash.phash output)
    phash_norm — hash of _normalize_image(image)

    GPU path (PyTorch available):
      • Resize to 32×32 with PIL LANCZOS — identical to imagehash, ensuring
        that the pixel values fed into the DCT are bit-for-bit the same.
      • Batch the grayscale pixel arrays into a GPU tensor.
      • Compute the 2-D DCT via M @ batch @ M.T.
      • Binarise each 8×8 low-frequency block against its own median.
      • Pack 64 bits → 16-char hex via numpy.packbits.

    CPU fallback (no PyTorch):
      • Per-image imagehash.phash — exact match with existing DB entries.

    The hex strings produced by both paths represent the same 64-bit integer
    when parsed with int(h, 16), so they are interchangeable in the DB and in
    the vectorised Hamming matcher.
    """
    if not images:
        return []

    if _DCT_MATRIX is None:
        # No torch installed — fall back to imagehash per image
        results = []
        for img in images:
            h = h_norm = None
            try:
                h      = str(imagehash.phash(img))
                h_norm = str(imagehash.phash(_normalize_image(img)))
            except Exception:
                pass
            results.append((h, h_norm))
        return results

    import torch

    raw_pixels:  list[np.ndarray] = []
    norm_pixels: list[np.ndarray] = []
    valid:       list[int]        = []

    for i, img in enumerate(images):
        try:
            raw_pil  = img.convert("L").resize((32, 32), Image.LANCZOS)
            norm_pil = _normalize_image(img).convert("L").resize((32, 32), Image.LANCZOS)
            raw_pixels.append(np.asarray(raw_pil,  dtype=np.float64))
            norm_pixels.append(np.asarray(norm_pil, dtype=np.float64))
            valid.append(i)
        except Exception:
            pass

    results: list[tuple[Optional[str], Optional[str]]] = [(None, None)] * len(images)
    if not valid:
        return results

    device = _DCT_MATRIX.device
    raw_t  = torch.from_numpy(np.stack(raw_pixels)).to(device)   # (B, 32, 32)
    norm_t = torch.from_numpy(np.stack(norm_pixels)).to(device)  # (B, 32, 32)

    def _dct_hash_batch(batch: "torch.Tensor") -> list[str]:
        M     = _DCT_MATRIX
        dct2d = M @ batch @ M.T                             # (B, 32, 32)
        low   = dct2d[:, :8, :8].reshape(len(batch), 64)   # (B, 64)
        med   = low.median(dim=1).values.unsqueeze(1)       # (B, 1)
        bits  = (low > med).cpu().numpy().astype(np.uint8)  # (B, 64)
        return [np.packbits(b).tobytes().hex() for b in bits]

    with torch.no_grad():
        raw_hashes  = _dct_hash_batch(raw_t)
        norm_hashes = _dct_hash_batch(norm_t)

    for local_i, global_i in enumerate(valid):
        results[global_i] = (raw_hashes[local_i], norm_hashes[local_i])
    return results


def _phash_batch(
    img_bytes_list: list[bytes],
) -> list[tuple[Optional[str], Optional[str]]]:
    """Decode raw image bytes then call _phash_images."""
    images: list[Image.Image] = []
    valid:  list[int]         = []
    results: list[tuple[Optional[str], Optional[str]]] = [
        (None, None)
    ] * len(img_bytes_list)

    for i, img_bytes in enumerate(img_bytes_list):
        try:
            images.append(Image.open(io.BytesIO(img_bytes)).convert("RGB"))
            valid.append(i)
        except Exception:
            pass

    if not images:
        return results

    img_results = _phash_images(images)
    for local_i, global_i in enumerate(valid):
        results[global_i] = img_results[local_i]
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Vectorised Hamming matching
# ─────────────────────────────────────────────────────────────────────────────

def _hex_to_uint64(h: str) -> int:
    try:
        return int(h, 16)
    except Exception:
        return -1


def _hamming_vectorized(query_int: int, index_ints: np.ndarray) -> np.ndarray:
    """
    Compute Hamming distance between one uint64 query and an array of uint64s.
    XOR gives differing bits; unpackbits + sum counts them.
    """
    diff = np.bitwise_xor(index_ints, np.uint64(query_int))
    return np.unpackbits(diff.view(np.uint8)).reshape(len(index_ints), 64).sum(axis=1)


def _compute_query_hashes(image_bytes: bytes) -> tuple[list[str], list[str]]:
    """
    Return (raw_queries, norm_queries) for the user's image.

    raw_queries  — compared against index phash      (raw Scryfall thumbnails)
    norm_queries — compared against index phash_norm (normalised thumbnails)

    The card is isolated once; _phash_images computes both raw and norm hashes
    in a single GPU pass.  The normalised comparison lane is symmetric (both
    sides preprocessed identically) and more lighting-robust.
    """
    from scanner import isolate_card

    raw:  list[str] = []
    norm: list[str] = []

    try:
        img  = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        card = isolate_card(img)
        h_raw, h_norm = _phash_images([card])[0]
        if h_raw:
            raw.append(h_raw)
        if h_norm:
            raw.append(h_norm)   # normalised card vs. raw index (fallback)
            norm.append(h_norm)  # normalised card vs. normalised index
    except Exception as e:
        logger.warning("Query hash (isolated) failed: %s", e)

    # Full-image fallback (no isolation)
    try:
        img_full = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        h_full, _ = _phash_images([img_full])[0]
        if h_full:
            raw.append(h_full)
    except Exception:
        pass

    return raw, norm


async def find_best_match(image_bytes: bytes, hash_rows: list[dict]) -> Optional[dict]:
    """
    Compare image against all cached hashes using two parallel lanes:
      • raw   query vs. phash      (always available)
      • norm  query vs. phash_norm (available after /index rebuild)
    Both lanes use vectorised Hamming distance over numpy uint64 arrays.
    Returns the closest row (with added 'hash_distance' key) or None.
    """
    if not hash_rows:
        logger.warning("Hash match skipped: cache is empty — run /index update")
        return None

    raw_queries, norm_queries = await asyncio.to_thread(_compute_query_hashes, image_bytes)
    if not raw_queries and not norm_queries:
        logger.warning("Hash match skipped: could not compute any query hash")
        return None

    # Build uint64 arrays once; reused across all queries
    phash_ints = np.array(
        [_hex_to_uint64(r["phash"]) for r in hash_rows], dtype=np.uint64
    )
    norm_pairs = [(i, r) for i, r in enumerate(hash_rows) if r.get("phash_norm")]
    phash_norm_ints = (
        np.array([_hex_to_uint64(r["phash_norm"]) for _, r in norm_pairs], dtype=np.uint64)
        if norm_pairs else np.array([], dtype=np.uint64)
    )

    best_idx:  Optional[int] = None
    best_dist: int           = 999

    for query in raw_queries:
        q = _hex_to_uint64(query)
        if q < 0:
            continue
        dists = _hamming_vectorized(q, phash_ints)
        i = int(np.argmin(dists))
        if dists[i] < best_dist:
            best_dist = int(dists[i])
            best_idx  = i

    for query in norm_queries:
        q = _hex_to_uint64(query)
        if q < 0 or not norm_pairs:
            continue
        dists    = _hamming_vectorized(q, phash_norm_ints)
        local_i  = int(np.argmin(dists))
        if dists[local_i] < best_dist:
            best_dist = int(dists[local_i])
            best_idx  = norm_pairs[local_i][0]

    if best_idx is not None and best_dist <= MATCH_THRESHOLD:
        best_row = hash_rows[best_idx]
        logger.info(
            "Hash match: '%s' d=%d/%d",
            best_row.get("name_en", "?"), best_dist, MATCH_THRESHOLD,
        )
        return {**best_row, "hash_distance": best_dist}

    logger.info(
        "Hash match: no result within threshold (best d=%d for '%s', threshold=%d)",
        best_dist,
        hash_rows[best_idx].get("name_en", "?") if best_idx is not None else "—",
        MATCH_THRESHOLD,
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# New-set detection
# ─────────────────────────────────────────────────────────────────────────────

async def check_new_sets(db) -> list[dict]:
    """
    Return sets released on Scryfall that are not yet present in the hash index.
    Only considers 'playable' set types.
    """
    indexed_codes = await db.get_indexed_set_codes()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with aiohttp.ClientSession() as session:
        async with session.get(SETS_ENDPOINT) as resp:
            if resp.status != 200:
                logger.error("Scryfall sets endpoint returned %s", resp.status)
                return []
            data = await resp.json()

    return [
        {"code": s["code"], "name": s["name"], "released_at": s.get("released_at", "")}
        for s in data.get("data", [])
        if s.get("set_type") in _PLAYABLE_SET_TYPES
        and s.get("released_at", "9999") <= today
        and s.get("code") not in indexed_codes
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Index build
# ─────────────────────────────────────────────────────────────────────────────

async def build_index(db, progress_cb=None) -> int:
    """
    Download Scryfall bulk data, hash all card images, persist to DB.
    Already-indexed cards (by scryfall_id) are skipped.

    Download and hashing are decoupled:
      1. A batch of up to 400 card images is downloaded concurrently.
      2. All downloaded images are then hashed in GPU batches of _GPU_HASH_BATCH
         via asyncio.to_thread so the event loop is never blocked.

    Calls progress_cb(done, total, indexed_count, status_str) after each batch.
    Returns number of newly indexed cards.
    """
    sem     = asyncio.Semaphore(_CONCURRENCY)
    counter = {"indexed": 0}

    async with aiohttp.ClientSession(
        headers={"User-Agent": "MTGCollectionBot/1.0 contact:bot@local"}
    ) as session:

        # ── Step 1: bulk data URL ────────────────────────────────────────────
        if progress_cb:
            await progress_cb(0, 1, 0, "Fetching card list from Scryfall…")
        async with session.get(BULK_ENDPOINT) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Scryfall bulk endpoint returned {resp.status}")
            meta = await resp.json()
        bulk_url = meta["download_uri"]

        # ── Step 2: bulk JSON ────────────────────────────────────────────────
        if progress_cb:
            await progress_cb(0, 1, 0, "Downloading bulk data (~100 MB)…")
        async with session.get(bulk_url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Bulk data download returned {resp.status}")
            content_length = int(resp.headers.get("Content-Length", 0))
            chunks: list[bytes] = []
            downloaded = 0
            async for chunk in resp.content.iter_chunked(131_072):
                chunks.append(chunk)
                downloaded += len(chunk)
                if progress_cb and content_length:
                    mb = downloaded / 1_048_576
                    mb_total = content_length / 1_048_576
                    await progress_cb(0, 1, 0, f"Downloading bulk data… {mb:.0f} / {mb_total:.0f} MB")
            raw = b"".join(chunks)
        cards = json.loads(raw)
        total = len(cards)
        logger.info("Bulk data: %d cards", total)

        # ── Step 3: skip already-indexed cards ──────────────────────────────
        existing_ids = await db.get_indexed_scryfall_ids()

        # pending accumulates (card_dict, img_bytes) for each download batch
        pending: list[tuple[dict, bytes]] = []

        async def download_one(card: dict) -> None:
            if card.get("layout") in ("art_series", "token"):
                return
            if card["id"] in existing_ids:
                return
            uris = card.get("image_uris") or (
                (card.get("card_faces") or [{}])[0].get("image_uris")
            ) or {}
            url = uris.get("small")
            if not url:
                return
            async with sem:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=20)
                    ) as resp:
                        img_bytes = await resp.read() if resp.status == 200 else None
                except Exception:
                    img_bytes = None
                await asyncio.sleep(_RATE_DELAY)
            if img_bytes:
                pending.append((card, img_bytes))

        async def flush_pending() -> None:
            """Hash all pending images in GPU batches and upsert to DB."""
            if not pending:
                return
            for batch_start in range(0, len(pending), _GPU_HASH_BATCH):
                batch          = pending[batch_start : batch_start + _GPU_HASH_BATCH]
                img_bytes_list = [img for _, img in batch]
                hashes = await asyncio.to_thread(_phash_batch, img_bytes_list)
                for (card, _), (h, h_norm) in zip(batch, hashes):
                    if not h:
                        continue
                    await db.upsert_card_hash(
                        scryfall_id=card["id"],
                        name_en=card.get("name", ""),
                        set_code=card.get("set", ""),
                        collector_number=card.get("collector_number", ""),
                        lang=card.get("lang", "en"),
                        phash=h,
                        phash_norm=h_norm,
                    )
                    counter["indexed"] += 1
            await db.commit()
            pending.clear()

        # ── Step 4: download → hash loop ─────────────────────────────────────
        device_label = "GPU" if _GPU_DEVICE and "cuda" in _GPU_DEVICE else "CPU"
        dl_batch = 400
        for start in range(0, total, dl_batch):
            chunk = cards[start : start + dl_batch]
            await asyncio.gather(*[download_one(c) for c in chunk])
            await flush_pending()
            if progress_cb:
                await progress_cb(
                    min(start + dl_batch, total),
                    total,
                    counter["indexed"],
                    f"Hashing card images ({device_label})…",
                )

    await db.set_index_meta("last_built_at", datetime.now(timezone.utc).isoformat())
    return counter["indexed"]
