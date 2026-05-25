"""Lightweight exchange-rate helper.

Uses the Frankfurter API (https://www.frankfurter.app), backed by
European Central Bank (ECB) reference rates — completely free, no key
required.

Usage
-----
    from core.fx import get_usd_eur_rate

    rate = await get_usd_eur_rate()   # e.g. 0.9234
    eur  = usd_price * rate
"""
from __future__ import annotations

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_FRANKFURTER_URL = "https://api.frankfurter.app/latest?from=USD&to=EUR"
_FALLBACK_RATE   = 0.92   # reasonable fallback when network is unavailable
_TTL             = 86_400.0  # 24-hour cache

# Simple module-level cache — no lock needed; worst case we fetch twice
_cache: dict = {}   # {"rate": float, "ts": float}


async def get_usd_eur_rate() -> float:
    """Return the current USD → EUR exchange rate.

    Result is cached for 24 hours.  Returns ``0.92`` as a fallback if
    the Frankfurter API is unreachable.
    """
    import aiohttp

    now = time.monotonic()
    if _cache and now - _cache.get("ts", 0) < _TTL:
        return _cache["rate"]

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5)
        ) as session:
            async with session.get(_FRANKFURTER_URL) as resp:
                data = await resp.json(content_type=None)
                rate = float(data["rates"]["EUR"])
        _cache["rate"] = rate
        _cache["ts"]   = now
        logger.debug("USD/EUR rate fetched: %.4f", rate)
        return rate
    except Exception as exc:
        logger.warning("Could not fetch USD/EUR rate (%s), using %.2f", exc, _FALLBACK_RATE)
        return _cache.get("rate", _FALLBACK_RATE)


def usd_to_eur(amount: Optional[float], rate: float) -> Optional[float]:
    """Convert *amount* from USD to EUR using *rate*.  Passes through None."""
    if amount is None:
        return None
    return round(amount * rate, 4)
