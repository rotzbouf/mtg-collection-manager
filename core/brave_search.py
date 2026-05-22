"""Brave Search API client — find buylist URLs by keyword."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

_BUYLIST_HINTS = (
    "buylist", "ankauf", "kaufen", "wanted", "buying",
    "buy-list", "buy_list", "karten-ankauf",
)


def _looks_like_buylist(url: str, title: str, description: str) -> bool:
    combined = (url + " " + title + " " + description).lower()
    return any(h in combined for h in _BUYLIST_HINTS)


async def search_buylist_urls(
    api_key: str,
    query: str,
    count: int = 5,
    *,
    country: str = "DE",
    lang: str = "de",
    filter_hints: bool = True,
) -> list[dict]:
    """Call Brave Search API and return up to *count* results.

    Each result: {title, url, description, score}
    Results are soft-filtered: entries that look like buylists float to the top,
    but all results are returned so the caller can decide.
    """
    import aiohttp

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q":           query,
        "count":       min(count, 20),
        "country":     country,
        "search_lang": lang,
        "freshness":   "pw",  # past week for fresh buylists
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            _BRAVE_URL, headers=headers, params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 401:
                raise ValueError("Invalid Brave API key (401 Unauthorized)")
            if resp.status == 429:
                raise RuntimeError("Brave API rate limit reached — try again later")
            resp.raise_for_status()
            data = await resp.json()

    raw = data.get("web", {}).get("results", [])
    results = []
    for r in raw:
        url   = r.get("url", "")
        title = r.get("title", "")
        desc  = r.get("description", "") or ""
        score = 1 if _looks_like_buylist(url, title, desc) else 0
        results.append({"title": title, "url": url, "description": desc, "score": score})

    results.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Brave search '%s': %d results", query, len(results))
    return results
