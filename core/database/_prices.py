"""Price recording, history, statistics, and top-value queries."""
from typing import Optional

from ._common import _row_to_dict


class _PricesMixin:
    """Requires: self._db, self._write_lock."""

    async def record_prices(self) -> int:
        """Snapshot today's EUR price for every distinct scryfall_id in card_prices."""
        async with self._write_lock:
            async with self._db.execute(
                """
                SELECT scryfall_id, price_eur
                FROM card_prices
                WHERE scryfall_id IS NOT NULL AND price_eur IS NOT NULL
                """
            ) as cur:
                rows = await cur.fetchall()
            for row in rows:
                await self._db.execute(
                    "INSERT OR IGNORE INTO price_history (scryfall_id, price_eur) VALUES (?, ?)",
                    (row["scryfall_id"], row["price_eur"]),
                )
            await self._db.commit()
            return len(rows)

    async def get_null_price_cards(self) -> list[dict]:
        """Return distinct cards with no price in card_prices that have a known scryfall_id."""
        async with self._db.execute(
            """
            SELECT DISTINCT c.scryfall_id, c.oracle_id, c.name_en
            FROM collection c
            LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
            WHERE c.scryfall_id IS NOT NULL
              AND (cp.scryfall_id IS NULL OR cp.price_eur IS NULL)
              AND c.name_en IS NOT NULL
            """
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def update_card_prices(
        self,
        scryfall_id: str,
        price_eur: Optional[float],
        price_usd: Optional[float],
        approx: int = 0,
    ) -> None:
        """Upsert price into card_prices. None values never overwrite an existing price."""
        async with self._write_lock:
            await self._db.execute(
                """
                INSERT INTO card_prices (scryfall_id, price_eur, price_usd, price_approx, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(scryfall_id) DO UPDATE SET
                    price_eur    = COALESCE(excluded.price_eur, price_eur),
                    price_usd    = COALESCE(excluded.price_usd, price_usd),
                    price_approx = excluded.price_approx,
                    updated_at   = datetime('now')
                """,
                (scryfall_id, price_eur, price_usd, approx),
            )
            await self._db.commit()

    async def get_price_history(self, scryfall_id: str) -> list[dict]:
        async with self._db.execute(
            """
            SELECT price_eur, recorded_at
            FROM price_history
            WHERE scryfall_id = ?
            ORDER BY recorded_at ASC
            """,
            (scryfall_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_collection_value_history(self) -> list[dict]:
        """Total collection EUR value per recorded day, weighted by number of physical copies."""
        async with self._db.execute(
            """
            SELECT ph.recorded_at,
                   ROUND(SUM(ph.price_eur * cnt.card_count), 2) AS total_value_eur
            FROM price_history ph
            INNER JOIN (
                SELECT scryfall_id, COUNT(*) AS card_count
                FROM collection
                GROUP BY scryfall_id
            ) cnt ON ph.scryfall_id = cnt.scryfall_id
            WHERE ph.price_eur IS NOT NULL
            GROUP BY ph.recorded_at
            ORDER BY ph.recorded_at ASC
            """,
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_top_by_value(self, limit: int = 5) -> list[dict]:
        async with self._db.execute(
            """
            SELECT c.id, c.name_en, c.name_de, c.printed_name,
                   c.set_code, c.set_name, c.collector_number,
                   c.rarity, c.type_line, c.mana_cost, c.cmc, c.language,
                   c.condition, c.foil, c.price_eur, c.price_usd, c.image_url,
                   c.scryfall_id, c.oracle_text, c.flavor_text,
                   c.power, c.toughness, c.loyalty,
                   c.colors, c.color_identity, c.keywords,
                   ct.name AS container_name
            FROM collection_with_prices c
            LEFT JOIN containers ct ON c.container_id = ct.id
            WHERE c.price_eur IS NOT NULL
            ORDER BY c.price_eur DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            return [_row_to_dict(r) for r in await cur.fetchall()]

    async def stats(self) -> dict:
        async with self._db.execute(
            """
            SELECT
                COUNT(*)                  AS total_cards,
                COUNT(DISTINCT oracle_id) AS unique_cards,

                -- English
                COUNT(CASE WHEN language='en'            THEN 1 END) AS en_total,
                COUNT(CASE WHEN language='en' AND foil=0 THEN 1 END) AS en_nonfoil,
                COUNT(CASE WHEN language='en' AND foil=1 THEN 1 END) AS en_foil,
                ROUND(SUM(CASE WHEN language='en' AND foil=0 THEN COALESCE(price_eur,0) ELSE 0 END),2) AS en_nonfoil_eur,
                ROUND(SUM(CASE WHEN language='en' AND foil=1 THEN COALESCE(price_eur,0) ELSE 0 END),2) AS en_foil_eur,

                -- German
                COUNT(CASE WHEN language='de'            THEN 1 END) AS de_total,
                COUNT(CASE WHEN language='de' AND foil=0 THEN 1 END) AS de_nonfoil,
                COUNT(CASE WHEN language='de' AND foil=1 THEN 1 END) AS de_foil,
                ROUND(SUM(CASE WHEN language='de' AND foil=0 THEN COALESCE(price_eur,0) ELSE 0 END),2) AS de_nonfoil_eur,
                ROUND(SUM(CASE WHEN language='de' AND foil=1 THEN COALESCE(price_eur,0) ELSE 0 END),2) AS de_foil_eur,

                -- Rarity
                COUNT(CASE WHEN rarity='common'   THEN 1 END) AS r_common,
                COUNT(CASE WHEN rarity='uncommon' THEN 1 END) AS r_uncommon,
                COUNT(CASE WHEN rarity='rare'     THEN 1 END) AS r_rare,
                COUNT(CASE WHEN rarity='mythic'   THEN 1 END) AS r_mythic,
                ROUND(SUM(CASE WHEN rarity='common'   THEN COALESCE(price_eur,0) ELSE 0 END),2) AS r_common_eur,
                ROUND(SUM(CASE WHEN rarity='uncommon' THEN COALESCE(price_eur,0) ELSE 0 END),2) AS r_uncommon_eur,
                ROUND(SUM(CASE WHEN rarity='rare'     THEN COALESCE(price_eur,0) ELSE 0 END),2) AS r_rare_eur,
                ROUND(SUM(CASE WHEN rarity='mythic'   THEN COALESCE(price_eur,0) ELSE 0 END),2) AS r_mythic_eur,

                -- Foil totals
                COUNT(CASE WHEN foil=1 THEN 1 END)                                    AS foil_total,
                ROUND(SUM(CASE WHEN foil=1 THEN COALESCE(price_eur,0) ELSE 0 END),2)  AS foil_eur,
                ROUND(SUM(COALESCE(price_eur,0)),2)                                    AS total_value_eur,
                ROUND(SUM(COALESCE(price_usd,0)),2)                                    AS total_value_usd
            FROM collection_with_prices
            """
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {}
        result = dict(row)

        async with self._db.execute(
            """
            SELECT c.name_en, c.name_de, c.printed_name, c.price_eur, c.price_approx,
                   c.foil, c.language, c.scryfall_id, c.image_url,
                   ct.name AS container_name
            FROM collection_with_prices c
            LEFT JOIN containers ct ON c.container_id = ct.id
            WHERE c.price_eur IS NOT NULL
            ORDER BY c.price_eur DESC
            LIMIT 10
            """
        ) as cur:
            result["top_cards"] = [dict(r) for r in await cur.fetchall()]

        return result
