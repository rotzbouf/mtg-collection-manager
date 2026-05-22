"""Format ban tracking: materialized banlists and manual overrides."""
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TRACKED_FORMATS = ("standard", "modern", "legacy", "vintage", "pauper", "commander")


class _LegalityMixin:
    """Requires: self._db, self._write_lock."""

    async def update_card_legalities(self, scryfall_id: str, legalities: dict) -> None:
        """Overwrite the legalities JSON for every collection row sharing this scryfall_id."""
        async with self._write_lock:
            await self._db.execute(
                "UPDATE collection SET legalities = ? WHERE scryfall_id = ?",
                (json.dumps(legalities), scryfall_id),
            )
            await self._db.commit()

    async def rebuild_format_bans(self) -> int:
        """Repopulate format_bans from current collection legalities.

        Clears all derived rows and rebuilds from the legalities JSON stored per
        unique scryfall_id.  Returns the number of ban/restricted rows written.
        """
        async with self._db.execute(
            """
            SELECT name_en, legalities
            FROM collection
            WHERE scryfall_id IS NOT NULL AND legalities IS NOT NULL
            GROUP BY scryfall_id
            """
        ) as cur:
            rows = await cur.fetchall()

        ban_rows: list[tuple[str, str, str]] = []
        for row in rows:
            name = row["name_en"] or ""
            raw  = row["legalities"] or "{}"
            try:
                leg = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                continue
            for fmt in TRACKED_FORMATS:
                status = leg.get(fmt)
                if status in ("banned", "restricted"):
                    ban_rows.append((fmt, name, status))

        async with self._write_lock:
            await self._db.execute("DELETE FROM format_bans")
            for fmt, name, status in ban_rows:
                await self._db.execute(
                    """
                    INSERT OR REPLACE INTO format_bans (format, card_name, status, refreshed_at)
                    VALUES (?, ?, ?, datetime('now'))
                    """,
                    (fmt, name, status),
                )
            await self._db.commit()

        logger.info("Format bans rebuilt: %d entries across %d formats", len(ban_rows), len(TRACKED_FORMATS))
        return len(ban_rows)

    async def get_format_bans(self, fmt: str) -> list[dict]:
        """Return banned/restricted cards for a format with overrides applied.

        Override status='legal' hides a card from the list.
        Override status='banned'/'restricted' adds or replaces an entry.
        """
        async with self._db.execute(
            """
            SELECT b.card_name,
                   COALESCE(o.status, b.status) AS status,
                   o.reason,
                   CASE WHEN o.card_name IS NOT NULL THEN 1 ELSE 0 END AS is_override
            FROM format_bans b
            LEFT JOIN format_ban_overrides o
                ON b.format = o.format AND b.card_name = o.card_name
            WHERE b.format = ?
              AND COALESCE(o.status, b.status) != 'legal'

            UNION

            SELECT o.card_name,
                   o.status,
                   o.reason,
                   1 AS is_override
            FROM format_ban_overrides o
            LEFT JOIN format_bans b
                ON o.format = b.format AND o.card_name = b.card_name
            WHERE o.format = ?
              AND b.card_name IS NULL
              AND o.status != 'legal'

            ORDER BY 1
            """,
            (fmt, fmt),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def add_ban_override(
        self,
        fmt: str,
        card_name: str,
        status: str,
        reason: Optional[str] = None,
    ) -> None:
        async with self._write_lock:
            await self._db.execute(
                """
                INSERT INTO format_ban_overrides (format, card_name, status, reason, added_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(format, card_name) DO UPDATE SET
                    status   = excluded.status,
                    reason   = excluded.reason,
                    added_at = datetime('now')
                """,
                (fmt, card_name, status, reason),
            )
            await self._db.commit()

    async def remove_ban_override(self, fmt: str, card_name: str) -> None:
        async with self._write_lock:
            await self._db.execute(
                "DELETE FROM format_ban_overrides WHERE format = ? AND card_name = ?",
                (fmt, card_name),
            )
            await self._db.commit()

    async def get_ban_overrides(self, fmt: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM format_ban_overrides WHERE format = ? ORDER BY card_name",
            (fmt,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
