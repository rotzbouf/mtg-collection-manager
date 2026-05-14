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
        if torch.cuda.is_available():
            torch.cuda.set_device(0)
            device = torch.device("cuda:0")
            logger.info("GPU pHash ready — %s (device 0)", torch.cuda.get_device_name(0))
        else:
            device = torch.device("cpu")
            logger.info("GPU pHash: CUDA not available, using CPU torch DCT")
        N = 32
        k = torch.arange(N, dtype=torch.float64)
        n = torch.arange(N, dtype=torch.float64)
        _DCT_MATRIX = (
            2.0 * torch.cos(
                torch.pi * k.unsqueeze(1) * (2.0 * n.unsqueeze(0) + 1.0) / (2.0 * N)
            )
        ).to(device)
        _GPU_DEVICE = str(device)
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


async def _find_candidates(
    image_bytes: bytes,
    hash_rows: list[dict],
    max_dist: int,
) -> list[dict]:
    """
    Core matching: compare query image against hash_rows and return all
    candidates with Hamming distance ≤ max_dist, sorted ascending by distance.
    Used by both find_best_match and find_top_candidates.
    """
    if not hash_rows:
        logger.warning("Hash match skipped: cache is empty — run /index update")
        return []

    raw_queries, norm_queries = await asyncio.to_thread(_compute_query_hashes, image_bytes)
    if not raw_queries and not norm_queries:
        logger.warning("Hash match skipped: could not compute any query hash")
        return []

    phash_ints = np.array(
        [_hex_to_uint64(r["phash"]) for r in hash_rows], dtype=np.uint64
    )
    norm_pairs = [(i, r) for i, r in enumerate(hash_rows) if r.get("phash_norm")]
    phash_norm_ints = (
        np.array([_hex_to_uint64(r["phash_norm"]) for _, r in norm_pairs], dtype=np.uint64)
        if norm_pairs else np.array([], dtype=np.uint64)
    )

    best_dists = np.full(len(hash_rows), 999, dtype=np.int32)

    for query in raw_queries:
        q = _hex_to_uint64(query)
        if q < 0:
            continue
        dists = _hamming_vectorized(q, phash_ints).astype(np.int32)
        np.minimum(best_dists, dists, out=best_dists)

    for query in norm_queries:
        q = _hex_to_uint64(query)
        if q < 0 or not norm_pairs:
            continue
        dists = _hamming_vectorized(q, phash_norm_ints).astype(np.int32)
        for local_i, (global_i, _) in enumerate(norm_pairs):
            if dists[local_i] < best_dists[global_i]:
                best_dists[global_i] = int(dists[local_i])

    candidates = [
        {**hash_rows[i], "hash_distance": int(best_dists[i])}
        for i in range(len(hash_rows))
        if int(best_dists[i]) <= max_dist
    ]
    candidates.sort(key=lambda c: c["hash_distance"])
    return candidates


async def find_best_match(image_bytes: bytes, hash_rows: list[dict]) -> Optional[dict]:
    """Returns the single closest hash match within MATCH_THRESHOLD, or None."""
    candidates = await _find_candidates(image_bytes, hash_rows, max_dist=MATCH_THRESHOLD)
    if not candidates:
        logger.info("Hash match: no result within threshold %d", MATCH_THRESHOLD)
        return None
    best = candidates[0]
    logger.info("Hash match: '%s' d=%d/%d", best.get("name_en", "?"), best["hash_distance"], MATCH_THRESHOLD)
    return best


async def find_top_candidates(
    image_bytes: bytes,
    hash_rows: list[dict],
    top_n: int = 5,
) -> list[dict]:
    """
    Return the top_n closest hash matches within an expanded threshold
    (MATCH_THRESHOLD + 6).  Use this when OCR cross-validation is available:
    the OCR result can confirm the correct card even if it ranks 2nd or 3rd by
    hash distance, recovering from near-miss hashes caused by photo noise or
    partial card isolation failure.
    """
    candidates = await _find_candidates(
        image_bytes, hash_rows, max_dist=MATCH_THRESHOLD + 6
    )
    if candidates:
        logger.info(
            "Top hash candidates: %s",
            [(c.get("name_en", "?"), c["hash_distance"]) for c in candidates[:top_n]],
        )
    return candidates[:top_n]


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

async def build_index(
    db,
    progress_cb=None,
    pause_event: asyncio.Event = None,
) -> int:
    """
    Download Scryfall bulk data, hash all card images, persist to DB.
    Already-indexed cards (by scryfall_id) are skipped.

    Producer-consumer pipeline:
      • Producer  — downloads images concurrently (rate-limited, CPU/network)
      • Consumer  — hashes in GPU batches via asyncio.to_thread (GPU/CPU compute)
    Both run at the same time so the GPU never sits idle waiting for downloads.

    pause_event: asyncio.Event — set = running, cleared = paused.
                 The producer checks it before each download; the consumer
                 before each hash batch.  Pass None to disable pause support.

    Calls progress_cb(done, total, indexed_count, status_str) after each hash batch.
    Returns number of newly indexed cards.
    """
    if pause_event is None:
        pause_event = asyncio.Event()
        pause_event.set()

    counter = {"indexed": 0, "done": 0}
    device_label = "GPU" if _GPU_DEVICE and "cuda" in _GPU_DEVICE else "CPU"

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

        # ── Step 2: bulk JSON (streamed with progress) ───────────────────────
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

        # ── Step 4: pipeline ─────────────────────────────────────────────────
        # Queue capacity: enough to keep the GPU fed without unbounded memory.
        queue: asyncio.Queue = asyncio.Queue(maxsize=_GPU_HASH_BATCH * 8)

        # Shared iterator: _CONCURRENCY workers each pull the next card.
        # Exactly _CONCURRENCY coroutines exist — no 70k+ coroutine flood.
        card_iter = iter(cards)

        async def _worker() -> None:
            for card in card_iter:
                await pause_event.wait()   # block here while paused
                counter["done"] += 1
                if card.get("layout") in ("art_series", "token"):
                    continue
                if card["id"] in existing_ids:
                    continue
                uris = card.get("image_uris") or (
                    (card.get("card_faces") or [{}])[0].get("image_uris")
                ) or {}
                url = uris.get("small")
                if not url:
                    continue
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=20)
                    ) as resp:
                        img_bytes = await resp.read() if resp.status == 200 else None
                except Exception:
                    img_bytes = None
                await asyncio.sleep(_RATE_DELAY)
                if img_bytes:
                    await queue.put((card, img_bytes))

        async def _producer() -> None:
            await asyncio.gather(*[_worker() for _ in range(_CONCURRENCY)])
            await queue.put(None)  # poison pill — signals consumer to stop

        async def _hash_and_save(batch: list) -> None:
            """Hash one GPU batch and persist to DB."""
            hashes = await asyncio.to_thread(_phash_batch, [img for _, img in batch])
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
            if progress_cb:
                await progress_cb(
                    counter["done"], total, counter["indexed"],
                    f"Downloading + hashing ({device_label})…",
                )

        async def _consumer() -> None:
            batch: list = []
            while True:
                item = await queue.get()
                if item is None:
                    break
                batch.append(item)
                if len(batch) >= _GPU_HASH_BATCH:
                    await pause_event.wait()   # block here while paused
                    await _hash_and_save(batch)
                    batch.clear()
            if batch:  # flush remainder
                await _hash_and_save(batch)

        await asyncio.gather(_producer(), _consumer())

    await db.set_index_meta("last_built_at", datetime.now(timezone.utc).isoformat())
    return counter["indexed"]
