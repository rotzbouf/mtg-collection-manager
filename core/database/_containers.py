"""Container CRUD and overcount queries."""
from typing import Optional

from ._common import _row_to_dict


class _ContainersMixin:
    """Requires: self._db, self._write_lock."""

    async def create_container(
        self, name: str, description: str = "", type: str = "binder"
    ) -> int:
        async with self._write_lock:
            async with self._db.execute(
                "INSERT INTO containers (name, description, type) VALUES (?, ?, ?)",
                (name, description or None, type),
            ) as cur:
                row_id = cur.lastrowid
            await self._db.commit()
            return row_id

    async def container_stats(self) -> list[dict]:
        """Per-container card count, total value, and max single-card value."""
        async with self._db.execute(
            """
            SELECT
                ct.id,
                ct.name,
                ct.type,
                ct.deck_format,
                COUNT(c.id)                                                            AS card_count,
                ROUND(SUM(COALESCE(cp.price_eur, c.price_eur, 0)), 2)                 AS total_value_eur,
                MAX(COALESCE(cp.price_eur, c.price_eur, 0))                           AS max_card_eur
            FROM containers ct
            LEFT JOIN collection c ON c.container_id = ct.id
            LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
            GROUP BY ct.id, ct.name, ct.type, ct.deck_format
            ORDER BY total_value_eur DESC
            """
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_containers(self) -> list[dict]:
        async with self._db.execute(
            """
            SELECT ct.*,
                COUNT(c.id) as card_count,
                ROUND(SUM(COALESCE(cp.price_eur, c.price_eur, 0)), 2) as total_value_eur
            FROM containers ct
            LEFT JOIN collection c ON c.container_id = ct.id
            LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
            GROUP BY ct.id
            ORDER BY ct.name
            """
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_container(self, container_id: int) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM containers WHERE id = ?", (container_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def move_cards_to_container(
        self, card_ids: list[int], container_id: Optional[int]
    ) -> int:
        """Move a list of collection entries to a container. Returns number of rows updated."""
        if not card_ids:
            return 0
        placeholders = ",".join("?" * len(card_ids))
        async with self._write_lock:
            result = await self._db.execute(
                f"UPDATE collection SET container_id = ?, updated_at = datetime('now') WHERE id IN ({placeholders})",
                [container_id, *card_ids],
            )
            await self._db.commit()
            return result.rowcount

    async def delete_container(self, container_id: int) -> bool:
        """Delete the container. Cards inside have their container_id set to NULL (kept)."""
        async with self._write_lock:
            async with self._db.execute(
                "DELETE FROM containers WHERE id = ?", (container_id,)
            ) as cur:
                deleted = cur.rowcount > 0
            await self._db.commit()
            return deleted

    async def delete_container_and_cards(self, container_id: int) -> tuple[int, bool]:
        """Delete the container and all cards inside it.

        Returns (cards_deleted, container_deleted).
        """
        async with self._write_lock:
            async with self._db.execute(
                "DELETE FROM collection WHERE container_id = ?", (container_id,)
            ) as cur:
                cards_deleted = cur.rowcount
            async with self._db.execute(
                "DELETE FROM containers WHERE id = ?", (container_id,)
            ) as cur:
                container_deleted = cur.rowcount > 0
            await self._db.commit()
        return cards_deleted, container_deleted

    async def count_cards_in_container(self, container_id: int) -> int:
        """Return the number of cards in a container."""
        async with self._db.execute(
            "SELECT COUNT(*) FROM collection WHERE container_id = ?", (container_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def rename_container(self, container_id: int, new_name: str) -> bool:
        async with self._write_lock:
            async with self._db.execute(
                "UPDATE containers SET name = ? WHERE id = ?", (new_name, container_id)
            ) as cur:
                updated = cur.rowcount > 0
            await self._db.commit()
            return updated

    async def set_commander(
        self, card_id: int, is_commander: bool, container_id: int
    ) -> tuple[bool, str]:
        """Mark or unmark a card as commander. Enforces max 2 commanders per container."""
        async with self._write_lock:
            if is_commander:
                async with self._db.execute(
                    "SELECT COUNT(*) FROM collection WHERE container_id=? AND is_commander=1 AND id!=?",
                    (container_id, card_id),
                ) as cur:
                    count = (await cur.fetchone())[0]
                if count >= 2:
                    return False, "A deck can have at most 2 commanders (e.g. Partner)."
            await self._db.execute(
                "UPDATE collection SET is_commander=? WHERE id=?",
                (1 if is_commander else 0, card_id),
            )
            await self._db.commit()
            return True, ""

    async def update_container_type(self, container_id: int, new_type: str) -> bool:
        async with self._write_lock:
            async with self._db.execute(
                "UPDATE containers SET type = ? WHERE id = ?", (new_type, container_id)
            ) as cur:
                updated = cur.rowcount > 0
            await self._db.commit()
            return updated

    async def set_container_deck_format(
        self, container_id: int, deck_format: Optional[str]
    ) -> None:
        async with self._write_lock:
            await self._db.execute(
                "UPDATE containers SET deck_format = ? WHERE id = ?",
                (deck_format, container_id),
            )
            await self._db.commit()

    async def set_container_color_identity(
        self, container_id: int, color_identity: Optional[str]
    ) -> None:
        async with self._write_lock:
            await self._db.execute(
                "UPDATE containers SET color_identity = ? WHERE id = ?",
                (color_identity, container_id),
            )
            await self._db.commit()

    async def get_cards_in_overcount_containers(
        self,
        min_price: float = 0.0,
        max_price: float | None = None,
        rarities: list[str] | None = None,
        set_codes: list[str] | None = None,
        order_by: str = "price_desc",
        limit: int = 2000,
    ) -> list[dict]:
        """Return cards from containers with type='overcount', with optional filters."""
        conds: list[str] = ["ct.type = 'overcount'"]
        params: list = []

        if min_price > 0:
            conds.append("COALESCE(c.price_eur, 0) >= ?")
            params.append(min_price)
        if max_price is not None:
            conds.append("COALESCE(c.price_eur, 0) <= ?")
            params.append(max_price)
        if rarities:
            ph = ",".join("?" * len(rarities))
            conds.append(f"LOWER(COALESCE(c.rarity,'')) IN ({ph})")
            params.extend(r.lower() for r in rarities)
        if set_codes:
            ph = ",".join("?" * len(set_codes))
            conds.append(f"c.set_code IN ({ph})")
            params.extend(set_codes)

        _ORDER_MAP = {
            "price_desc": "COALESCE(c.price_eur, 0) DESC, c.name_en",
            "price_asc":  "COALESCE(c.price_eur, 0) ASC,  c.name_en",
            "name":       "c.name_en ASC",
            "set":        "c.set_code ASC, CAST(c.collector_number AS INTEGER) ASC",
        }
        if order_by not in _ORDER_MAP:
            raise ValueError(f"Invalid order_by: {order_by!r}")
        order = _ORDER_MAP[order_by]

        params.append(limit)
        sql = f"""
            SELECT c.*, ct.name AS container_name
            FROM collection_with_prices c
            JOIN containers ct ON c.container_id = ct.id
            WHERE {" AND ".join(conds)}
            ORDER BY {order}
            LIMIT ?
        """
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def get_overcount_container_sets(self) -> list[dict]:
        """Return distinct sets present in overcount-type containers."""
        async with self._db.execute(
            """
            SELECT c.set_code, c.set_name, COUNT(*) AS card_count
            FROM collection c
            JOIN containers ct ON c.container_id = ct.id
            WHERE ct.type = 'overcount' AND c.set_code IS NOT NULL
            GROUP BY c.set_code
            ORDER BY c.set_name
            """
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_overcount_cards(
        self, threshold: int = 4, excluded_types: list[str] | None = None
    ) -> list[dict]:
        """Return cards appearing more than *threshold* times with full per-entry details.

        Basic lands are excluded. Cards in containers whose type is in *excluded_types*
        are ignored. Each result dict has: name_en, printed_name, name_de, total, entries.
        """
        excl = excluded_types or []
        if excl:
            ph = ",".join("?" * len(excl))
            excl_cte   = f"excl_containers AS (SELECT id FROM containers WHERE type IN ({ph})),"
            excl_and   = "AND (c.container_id IS NULL OR c.container_id NOT IN (SELECT id FROM excl_containers))"
            excl_where = "WHERE (c.container_id IS NULL OR c.container_id NOT IN (SELECT id FROM excl_containers))"
        else:
            excl_cte = ""
            excl_and  = ""
            excl_where = ""

        sql = f"""
        WITH {excl_cte}
        totals AS (
            SELECT c.name_en, COUNT(*) AS total
            FROM collection c
            WHERE COALESCE(c.type_line, '') NOT LIKE 'Basic Land%'
            {excl_and}
            GROUP BY c.name_en
            HAVING total > ?
        )
        SELECT c.id, c.name_en, c.name_de, c.printed_name,
               c.set_code, c.set_name, c.collector_number,
               c.rarity, c.price_eur, c.price_usd,
               c.condition, c.foil, c.language,
               c.scryfall_id, c.image_url,
               c.container_id, ct.name AS container_name,
               t.total
        FROM collection_with_prices c
        JOIN totals t ON c.name_en = t.name_en
        LEFT JOIN containers ct ON c.container_id = ct.id
        {excl_where}
        ORDER BY t.total DESC, c.name_en, COALESCE(c.price_eur, 0) DESC
        """
        async with self._db.execute(sql, (*excl, threshold)) as cur:
            rows = await cur.fetchall()

        cards: dict[str, dict] = {}
        for row in rows:
            d = dict(row)
            name = d["name_en"]
            if name not in cards:
                cards[name] = {
                    "name_en":      name,
                    "name_de":      d["name_de"],
                    "printed_name": d["printed_name"],
                    "total":        d["total"],
                    "entries":      [],
                }
            cards[name]["entries"].append(d)
        return list(cards.values())

    async def get_deck_card_affinity(self, fmt: Optional[str] = None) -> tuple[dict[str, float], int]:
        """Return ({lowercase_card_name: deck_count}, num_decks) for existing deck containers.

        deck_count is how many distinct deck containers the card appears in.
        num_decks is the total number of deck containers in the query scope.
        Optionally filtered to a specific format (deck_format column).
        Falls back to all decks if the format filter returns nothing.
        """
        def _queries(fmt_filter: Optional[str]) -> tuple[tuple[str, list], tuple[str, list]]:
            if fmt_filter:
                card_q = (
                    """
                    SELECT LOWER(c.name_en) AS nm, COUNT(DISTINCT c.container_id) AS cnt
                    FROM collection c
                    JOIN containers ct ON c.container_id = ct.id
                    WHERE ct.type IN ('deck', 'commander')
                      AND ct.deck_format = ?
                    GROUP BY nm
                    """,
                    [fmt_filter],
                )
                deck_q = (
                    "SELECT COUNT(*) FROM containers WHERE type IN ('deck','commander') AND deck_format = ?",
                    [fmt_filter],
                )
            else:
                card_q = (
                    """
                    SELECT LOWER(c.name_en) AS nm, COUNT(DISTINCT c.container_id) AS cnt
                    FROM collection c
                    JOIN containers ct ON c.container_id = ct.id
                    WHERE ct.type IN ('deck', 'commander')
                    GROUP BY nm
                    """,
                    [],
                )
                deck_q = (
                    "SELECT COUNT(*) FROM containers WHERE type IN ('deck','commander')",
                    [],
                )
            return card_q, deck_q

        card_q, deck_q = _queries(fmt)
        async with self._db.execute(card_q[0], card_q[1]) as cur:
            rows = await cur.fetchall()

        if not rows and fmt:
            card_q, deck_q = _queries(None)
            async with self._db.execute(card_q[0], card_q[1]) as cur:
                rows = await cur.fetchall()

        async with self._db.execute(deck_q[0], deck_q[1]) as cur:
            count_row = await cur.fetchone()
        num_decks = count_row[0] if count_row else 0

        return {row[0]: float(row[1]) for row in rows if row[0]}, num_decks
