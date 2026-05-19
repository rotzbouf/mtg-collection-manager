"""Local image cache — card art stored as files under IMAGE_CACHE_DIR."""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CACHE_DIR = pathlib.Path(os.getenv("IMAGE_CACHE_DIR", "images"))
_EXTS = ("webp", "jpg", "png")
_HEADERS = {"Accept": "image/webp,image/*,*/*;q=0.8"}
_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=5)
_MAX_BYTES = 20 * 1024 * 1024  # 20 MB hard cap


def get_cached_path(scryfall_id: str) -> Optional[pathlib.Path]:
    """Return local path if the image is already cached, else None."""
    for ext in _EXTS:
        p = CACHE_DIR / f"{scryfall_id}.{ext}"
        if p.exists():
            return p
    return None


async def ensure_cached(
    scryfall_id: str,
    image_url: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[pathlib.Path]:
    """Return local path for scryfall_id, downloading from image_url if not yet cached."""
    existing = get_cached_path(scryfall_id)
    if existing:
        return existing

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(headers=_HEADERS)
    try:
        async with session.get(image_url, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning("Image download %s → HTTP %s", scryfall_id, resp.status)
                return None
            ct = resp.headers.get("Content-Type", "")
            ext = "webp" if "webp" in ct else ("png" if "png" in ct else "jpg")
            path = CACHE_DIR / f"{scryfall_id}.{ext}"
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(65536):
                total += len(chunk)
                if total > _MAX_BYTES:
                    logger.warning(
                        "Image too large, aborting download: %s (>%d bytes)",
                        scryfall_id, _MAX_BYTES,
                    )
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
            await asyncio.to_thread(path.write_bytes, data)
            logger.debug("Cached %s → %s (%d bytes)", scryfall_id, path.name, len(data))
            return path
    except asyncio.TimeoutError:
        logger.warning("Image download timed out: %s", scryfall_id)
        return None
    except Exception as exc:
        logger.error("Image cache error %s: %s", scryfall_id, exc)
        return None
    finally:
        if own_session:
            await session.close()
