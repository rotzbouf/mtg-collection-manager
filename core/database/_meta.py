"""Meta-deck database operations.

Stores competitive deck data scraped from mtgtop8.com and derives
per-format card scores used by the deck builder.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class _MetaMixin:
    """Requires: self._db, self._write_lock."""

    # ------------------------------------------------------------------ #
    # Ingestion                                                             #
    # ------------------------------------------------------------------ #

    async def get_crawled_deck_ids(self, source: str, format_code: str) -> set[str]:
        """Return set of deck_ids already stored for a source+format."""
        async with self._db.execute(
            "SELECT deck_id FROM meta_decks WHERE source=? AND format=?",
            (source, format_code),
        ) as cur:
            return {row[0] for row in await cur.fetchall()}

    async def get_crawled_event_ids(self, source: str, format_code: str) -> set[str]:
        """Return set of event_ids that have at least one deck stored."""
        async with self._db.execute(
            "SELECT DISTINCT event_id FROM meta_decks WHERE source=? AND format=?",
            (source, format_code),
        ) as cur:
            return {row[0] for row in await cur.fetchall()}

    async def save_meta_deck(
        self,
        source: str,
        format_code: str,
        event_id: str,
        deck_id: str,
        player: Optional[str],
        place: Optional[str],
        cards: list[dict],  # [{name, quantity, section}]
    ) -> bool:
        """Insert a meta deck (cards included).  Returns True if new, False if already stored."""
        async with self._write_lock:
            async with self._db.execute(
                """
                INSERT OR IGNORE INTO meta_decks
                    (source, format, event_id, deck_id, player, place)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, format_code, event_id, deck_id, player, place),
            ) as cur:
                if cur.lastrowid == 0 or cur.rowcount == 0:
                    return False  # already existed
                db_id = cur.lastrowid

            for card in cards:
                await self._db.execute(
                    """
                    INSERT OR REPLACE INTO meta_deck_cards
                        (deck_db_id, card_name, quantity, section)
                    VALUES (?, ?, ?, ?)
                    """,
                    (db_id, card["name"], card.get("quantity", 1), card.get("section", "main")),
                )
            await self._db.commit()
        return True

    # ------------------------------------------------------------------ #
    # Score computation                                                     #
    # ------------------------------------------------------------------ #

    async def recompute_meta_scores(self, format_code: Optional[str] = None) -> int:
        """Recompute meta_card_scores for all formats (or a single one).

        Score formula (mainboard only):
          For each deck the card appears in, it earns a placement bonus:
            bonus = 1.0 / (sqrt(rank) + 0.5)   where rank = numeric place (1st=1, 2nd=2, …)
          If place is non-numeric or missing, bonus = 0.5.
          score = sum of bonuses across all decks × 100

        Returns the number of (card, format) rows written.
        """
        import math

        fmts: list[str]
        if format_code:
            fmts = [format_code]
        else:
            async with self._db.execute(
                "SELECT DISTINCT format FROM meta_decks"
            ) as cur:
                fmts = [row[0] for row in await cur.fetchall()]

        total_written = 0

        for fmt in fmts:
            # Hold the write lock for the entire read-compute-write cycle so two
            # concurrent callers never interleave their DELETE + INSERT for the
            # same format (TOCTOU race).
            async with self._write_lock:
                # Fetch all mainboard cards with their deck's placement
                async with self._db.execute(
                    """
                    SELECT mdc.card_name, md.place, SUM(mdc.quantity) AS qty
                    FROM meta_deck_cards mdc
                    JOIN meta_decks md ON mdc.deck_db_id = md.id
                    WHERE md.format = ? AND mdc.section = 'main'
                    GROUP BY mdc.card_name, md.id
                    """,
                    (fmt,),
                ) as cur:
                    rows = await cur.fetchall()

                # Accumulate scores per card
                scores: dict[str, float] = {}
                appearances: dict[str, int] = {}
                deck_sets: dict[str, set] = {}

                for card_name, place, qty in rows:
                    # Parse placement: "1", "2-4", "5-8", etc.
                    try:
                        rank = int(str(place or "").split("-")[0].strip())
                        bonus = 1.0 / (math.sqrt(rank) + 0.5)
                    except (ValueError, AttributeError):
                        bonus = 0.5

                    key = card_name.lower()
                    scores[key] = scores.get(key, 0.0) + bonus
                    appearances[key] = appearances.get(key, 0) + int(qty or 1)

                # Normalize to 0–100 range
                if scores:
                    max_score = max(scores.values())
                    if max_score > 0:
                        scores = {k: round(v / max_score * 100, 4) for k, v in scores.items()}

                # Count distinct decks per card
                async with self._db.execute(
                    """
                    SELECT mdc.card_name, COUNT(DISTINCT mdc.deck_db_id) AS cnt
                    FROM meta_deck_cards mdc
                    JOIN meta_decks md ON mdc.deck_db_id = md.id
                    WHERE md.format = ? AND mdc.section = 'main'
                    GROUP BY mdc.card_name
                    """,
                    (fmt,),
                ) as cur:
                    for card_name, cnt in await cur.fetchall():
                        deck_sets[card_name.lower()] = cnt  # type: ignore[assignment]

                # Clear old scores for this format and write new ones
                await self._db.execute(
                    "DELETE FROM meta_card_scores WHERE format=?", (fmt,)
                )
                for key, score in scores.items():
                    await self._db.execute(
                        """
                        INSERT INTO meta_card_scores
                            (card_name, format, score, appearances, deck_count)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            key,
                            fmt,
                            score,
                            appearances.get(key, 0),
                            deck_sets.get(key, 0),  # type: ignore[call-overload]
                        ),
                    )
                await self._db.commit()
                total_written += len(scores)

        logger.info("Meta scores recomputed: %d (card, format) rows", total_written)
        return total_written

    # ------------------------------------------------------------------ #
    # Queries                                                               #
    # ------------------------------------------------------------------ #

    async def get_meta_scores(self, format_code: str) -> dict[str, float]:
        """Return {card_name_lower: score} for the given format (0–100 scale)."""
        async with self._db.execute(
            "SELECT card_name, score FROM meta_card_scores WHERE format=?",
            (format_code,),
        ) as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}

    async def get_meta_stats(self) -> dict:
        """Return summary stats for the meta data UI."""
        async with self._db.execute(
            "SELECT format, COUNT(*) FROM meta_decks GROUP BY format"
        ) as cur:
            deck_counts = {row[0]: row[1] for row in await cur.fetchall()}

        async with self._db.execute(
            "SELECT MAX(crawled_at) FROM meta_decks"
        ) as cur:
            row = await cur.fetchone()
            last_crawl = row[0] if row else None

        async with self._db.execute(
            "SELECT COUNT(*) FROM meta_card_scores"
        ) as cur:
            row = await cur.fetchone()
            score_rows = row[0] if row else 0

        return {
            "deck_counts": deck_counts,
            "last_crawl": last_crawl,
            "score_rows": score_rows,
            "total_decks": sum(deck_counts.values()),
        }

    async def clear_meta_data(self, format_code: Optional[str] = None) -> int:
        """Delete meta decks (and cascade to cards/scores). Returns deleted deck count."""
        async with self._write_lock:
            if format_code:
                async with self._db.execute(
                    "SELECT COUNT(*) FROM meta_decks WHERE format=?", (format_code,)
                ) as cur:
                    n = (await cur.fetchone())[0]
                await self._db.execute(
                    "DELETE FROM meta_decks WHERE format=?", (format_code,)
                )
                await self._db.execute(
                    "DELETE FROM meta_card_scores WHERE format=?", (format_code,)
                )
            else:
                async with self._db.execute(
                    "SELECT COUNT(*) FROM meta_decks"
                ) as cur:
                    n = (await cur.fetchone())[0]
                await self._db.execute("DELETE FROM meta_decks")
                await self._db.execute("DELETE FROM meta_card_scores")
            await self._db.commit()
        return n
