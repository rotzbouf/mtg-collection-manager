"""Image serving route."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

import server.ui.deps as deps
from core.image_cache import get_cached_path, ensure_cached

router = APIRouter()

logger = logging.getLogger(__name__)

# C-3: allowlist for image redirect targets
_ALLOWED_IMAGE_HOSTS = {
    "cards.scryfall.io",
    "c1.scryfall.com",
    "c2.scryfall.com",
}


def _is_safe_image_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in _ALLOWED_IMAGE_HOSTS
    except Exception:
        return False


async def _get_image_url(scryfall_id: str) -> str | None:
    """Fetch the image_url for a scryfall_id from the collection table."""
    async with deps.db._db.execute(
        "SELECT image_url FROM collection WHERE scryfall_id = ? AND image_url IS NOT NULL LIMIT 1",
        (scryfall_id,),
    ) as cur:
        row = await cur.fetchone()
    return row[0] if row else None


async def _cache_in_background(scryfall_id: str, image_url: str) -> None:
    try:
        await ensure_cached(scryfall_id, image_url)
    except Exception as exc:
        logger.warning("Background image cache failed for %s: %s", scryfall_id, exc)


@router.get("/images/{scryfall_id}")
async def serve_image(scryfall_id: str, background_tasks: BackgroundTasks):
    # Try local cache first
    cached = await asyncio.to_thread(get_cached_path, scryfall_id)
    if cached:
        mime, _ = mimetypes.guess_type(str(cached))
        return FileResponse(str(cached), media_type=mime or "image/jpeg")

    # Look up image_url from DB
    image_url = await _get_image_url(scryfall_id)
    if image_url:
        # C-3: validate image URL before redirecting
        if not _is_safe_image_url(image_url):
            raise HTTPException(status_code=400, detail="Invalid image URL")
        # Trigger background caching for next time
        background_tasks.add_task(_cache_in_background, scryfall_id, image_url)
        return RedirectResponse(url=image_url, status_code=302)

    return Response(status_code=404)
