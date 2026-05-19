"""Image serving route."""
from __future__ import annotations

import asyncio
import mimetypes

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import FileResponse, RedirectResponse, Response

import server.ui.deps as deps
from core.image_cache import get_cached_path, ensure_cached

router = APIRouter()


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
    except Exception:
        pass


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
        # Trigger background caching for next time
        background_tasks.add_task(_cache_in_background, scryfall_id, image_url)
        return RedirectResponse(url=image_url, status_code=302)

    return Response(status_code=404)
