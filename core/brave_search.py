"""Brave Search API client — find buylist URLs by keyword."""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

_BUYLIST_HINTS = (
    "buylist", "ankauf", "kaufen", "wanted", "buying",
    "buy-list", "buy_list", "karten-ankauf", "wantlist",
    "sell", "verkaufen", "we buy", "wir kaufen",
)

# Patterns that suggest a result is a store's buylist page rather than
# a news article, forum post, or general shop landing page.
_STRONG_BUYLIST_HINTS = (
    "buylist", "ankauf", "buy-list", "buy_list", "karten-ankauf", "wantlist",
)


def _buylist_score(url: str, title: str, description: str) -> int:
    """Return a relevance score (higher = more likely a real buylist page)."""
    combined = (url + " " + title + " " + description).lower()
    score = 0
    for h in _STRONG_BUYLIST_HINTS:
        if h in combined:
            score += 2
    for h in _BUYLIST_HINTS:
        if h in combined:
            score += 1
    # Prefer pages where the buylist hint appears in the URL path itself
    url_lower = url.lower()
    for h in _STRONG_BUYLIST_HINTS:
        if h in url_lower:
            score += 3
    return score


async def search_buylist_urls(
    api_key: str,
    query: str,
    count: int = 10,
    *,
    country: str = "DE",
    lang: str = "de",
    filter_hints: bool = True,
) -> list[dict]:
    """Call Brave Search API and return up to *count* results.

    Each result: {title, url, description, score}
    Results are scored: entries that look like actual buylist pages
    (hint in URL, strong keywords) rank higher.
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
        # No freshness filter — buylist pages are persistent, not news.
        # "freshness: pw" was previously used but excluded most valid results.
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
        score = _buylist_score(url, title, desc)
        results.append({"title": title, "url": url, "description": desc, "score": score})

    results.sort(key=lambda x: x["score"], reverse=True)
    logger.info("Brave search '%s': %d results", query, len(results))
    return results
