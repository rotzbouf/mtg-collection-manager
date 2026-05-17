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
_TIMEOUT = aiohttp.ClientTimeout(total=30)


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
            data = await resp.read()
            await asyncio.to_thread(path.write_bytes, data)
            logger.debug("Cached %s → %s (%d bytes)", scryfall_id, path.name, len(data))
            return path
    except Exception as exc:
        logger.error("Image cache error %s: %s", scryfall_id, exc)
        return None
    finally:
        if own_session:
            await session.close()
