"""Cardmarket price cache — bulk download and local lookup."""
import json
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_CM_PRICE_URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_1.json"


class _CMPricesMixin:
    """Requires: self._db, self._write_lock."""

    async def sync_cm_prices(
        self,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> int:
        """Download CM price guide and store in cm_prices table.

        progress_cb(message, done, total) called at key steps.
        Returns number of rows written.
        """
        import aiohttp
        import asyncio

        if progress_cb:
            progress_cb("Downloading from Cardmarket…", 0, 0)

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _CM_PRICE_URL,
                headers={"Accept-Encoding": "gzip, deflate"},
                timeout=timeout,
            ) as resp:
                raw = await resp.read()

        if progress_cb:
            progress_cb("Parsing price guide…", 0, 0)

        data = await asyncio.to_thread(json.loads, raw)
        guides = data.get("priceGuides", [])

        rows = [
            (
                g["idProduct"],
                g.get("low"),
                g.get("trend"),
                g.get("avg7"),
                g.get("avg30"),
                g.get("low-foil"),
                g.get("trend-foil"),
                g.get("avg30-foil"),
            )
            for g in guides
            if g.get("idProduct")
        ]

        if progress_cb:
            progress_cb(f"Writing {len(rows):,} prices to database…", 0, len(rows))

        async with self._write_lock:
            await self._db.execute("DELETE FROM cm_prices")
            await self._db.executemany(
                """
                INSERT INTO cm_prices
                    (cm_id, low, trend, avg7, avg30, foil_low, foil_trend, foil_avg30, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                rows,
            )
            await self._db.commit()

        logger.info("CM prices synced: %d entries", len(rows))
        return len(rows)

    async def get_cm_prices_meta(self) -> dict:
        """Return {count, updated_at} for the cached CM price table."""
        async with self._db.execute(
            "SELECT COUNT(*), MAX(updated_at) FROM cm_prices"
        ) as cur:
            row = await cur.fetchone()
        return {
            "count": row[0] if row else 0,
            "updated_at": row[1] if row else None,
        }

    async def update_card_cm_id(self, scryfall_id: str, cm_id: int) -> None:
        """Store the Cardmarket product ID for a card identified by its Scryfall ID."""
        async with self._write_lock:
            await self._db.execute(
                "UPDATE collection SET cardmarket_id = ? WHERE scryfall_id = ? AND cardmarket_id IS NULL",
                (cm_id, scryfall_id),
            )
            await self._db.commit()

    async def get_scryfall_ids_missing_cm_id(self) -> list[str]:
        """Return distinct Scryfall IDs from collection where cardmarket_id is NULL."""
        async with self._db.execute(
            "SELECT DISTINCT scryfall_id FROM collection WHERE scryfall_id IS NOT NULL AND cardmarket_id IS NULL"
        ) as cur:
            rows = await cur.fetchall()
        return [row[0] for row in rows]
