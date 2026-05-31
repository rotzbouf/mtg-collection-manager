"""Search and list operations — FTS, advanced filter, paginated list."""
from typing import Optional

from ..sanitize import sanitize_fts_query
from ._common import _SORT_MAP, _row_to_dict


class _SearchMixin:
    """Requires: self._db."""

    async def count_search(self, query: str) -> int:
        safe = sanitize_fts_query(query)
        if not safe:
            return 0
        async with self._db.execute(
            """
            SELECT COUNT(*) FROM collection c
            JOIN collection_fts fts ON c.id = fts.rowid
            WHERE collection_fts MATCH ?
            """,
            (safe,),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def search(self, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
        safe = sanitize_fts_query(query)
        if not safe:
            return []
        async with self._db.execute(
            """
            SELECT c.*, ct.name as container_name,
                   COALESCE(cp.price_eur, c.price_eur) AS price_eur,
                   COALESCE(cp.price_usd, c.price_usd) AS price_usd
            FROM collection c
            LEFT JOIN containers ct ON c.container_id = ct.id
            LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
            JOIN collection_fts fts ON c.id = fts.rowid
            WHERE collection_fts MATCH ?
            ORDER BY c.chaos_key
            LIMIT ? OFFSET ?
            """,
            (safe, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def count_cards(
        self,
        language: Optional[str] = None,
        container_id: Optional[int] = None,
    ) -> int:
        conditions, params = [], []
        if language:
            conditions.append("language = ?")
            params.append(language)
        if container_id == -1:
            conditions.append("container_id IS NULL")
        elif container_id is not None:
            conditions.append("container_id = ?")
            params.append(container_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        async with self._db.execute(
            f"SELECT COUNT(*) FROM collection {where}", params
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def advanced_search(
        self,
        name: str = "",
        type_line: str = "",
        oracle_text: str = "",
        set_code: str = "",
        colors: list[str] | None = None,
        colors_exclusive: bool = False,
        rarities: list[str] | None = None,
        cmc_min: float | None = None,
        cmc_max: float | None = None,
        condition: str = "",
        language: str = "",
        foil: int | None = None,
        container_ids: list[int] | None = None,
        exclude_container_ids: list[int] | None = None,
        commander_only: bool = False,
        price_min: float | None = None,
        price_max: float | None = None,
        limit: int = 300,
    ) -> list[dict]:
        """Flexible filter search across the collection. All parameters are optional."""
        from core.mtg_dict import expand_term

        conditions: list[str] = []
        params: list = []

        if name:
            conditions.append(
                "(c.name_en LIKE ? OR c.name_de LIKE ? OR c.printed_name LIKE ?)"
            )
            like = f"%{name}%"
            params.extend([like, like, like])

        if type_line:
            terms = expand_term(type_line)
            parts = ["c.type_line LIKE ?" for _ in terms]
            conditions.append("(" + " OR ".join(parts) + ")")
            params.extend(f"%{t}%" for t in terms)

        if oracle_text:
            terms = expand_term(oracle_text)
            parts = ["c.oracle_text LIKE ?" for _ in terms]
            conditions.append("(" + " OR ".join(parts) + ")")
            params.extend(f"%{t}%" for t in terms)

        if set_code:
            conditions.append("c.set_code = ?")
            params.append(set_code.lower())

        if colors:
            color_parts = []
            for color in colors:
                if color == "C":
                    color_parts.append("c.colors = '[]'")
                else:
                    color_parts.append("c.colors LIKE ?")
                    params.append(f'%"{color}"%')
            conditions.append("(" + " OR ".join(color_parts) + ")")
            if colors_exclusive:
                all_colors = {"W", "U", "B", "R", "G"}
                for excl in all_colors - {c for c in colors if c != "C"}:
                    conditions.append("c.colors NOT LIKE ?")
                    params.append(f'%"{excl}"%')

        if rarities:
            ph = ",".join("?" * len(rarities))
            conditions.append(f"c.rarity IN ({ph})")
            params.extend(rarities)

        if cmc_min is not None:
            conditions.append("c.cmc >= ?")
            params.append(cmc_min)
        if cmc_max is not None:
            conditions.append("c.cmc <= ?")
            params.append(cmc_max)

        if condition:
            conditions.append("c.condition = ?")
            params.append(condition)

        if language:
            conditions.append("c.language = ?")
            params.append(language)

        if foil is not None:
            conditions.append("c.foil = ?")
            params.append(foil)

        if container_ids:
            parts = []
            for cid in container_ids:
                if cid == -1:
                    parts.append("c.container_id IS NULL")
                else:
                    parts.append("c.container_id = ?")
                    params.append(cid)
            conditions.append("(" + " OR ".join(parts) + ")")

        if exclude_container_ids:
            non_null = [cid for cid in exclude_container_ids if cid != -1]
            if non_null:
                ph = ",".join("?" * len(non_null))
                conditions.append(f"(c.container_id IS NULL OR c.container_id NOT IN ({ph}))")
                params.extend(non_null)
            if -1 in exclude_container_ids:
                conditions.append("c.container_id IS NOT NULL")

        if commander_only:
            conditions.append("c.is_commander = 1")

        if price_min is not None:
            conditions.append("c.price_eur >= ?")
            params.append(price_min)
        if price_max is not None:
            conditions.append("c.price_eur <= ?")
            params.append(price_max)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        async with self._db.execute(
            f"""
            SELECT c.*, ct.name AS container_name
            FROM collection_with_prices c
            LEFT JOIN containers ct ON c.container_id = ct.id
            {where}
            ORDER BY c.name_en, c.set_code, c.collector_number
            LIMIT ?
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def get_cards_by_names(
        self,
        names: list[str],
        exclude_container_types: list[str] | None = None,
    ) -> list[dict]:
        """Return all collection rows whose name_en, name_de, or printed_name
        matches any of the given names (case-insensitive).
        Pass exclude_container_types to skip cards in certain container kinds
        (e.g. ["deck", "commander"] to ignore cards already in a deck)."""
        if not names:
            return []
        name_ph = ",".join("?" * len(names))
        lower   = [n.lower() for n in names]

        extra_where = ""
        extra_params: list = []
        if exclude_container_types:
            ct_ph = ",".join("?" * len(exclude_container_types))
            extra_where = f" AND (ct.type IS NULL OR ct.type NOT IN ({ct_ph}))"
            extra_params = list(exclude_container_types)

        async with self._db.execute(
            f"""
            SELECT c.*, ct.name as container_name,
                   COALESCE(cp.price_eur, c.price_eur) AS price_eur,
                   COALESCE(cp.price_usd, c.price_usd) AS price_usd
            FROM collection_with_prices c
            LEFT JOIN containers ct ON c.container_id = ct.id
            LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
            WHERE (
                lower(c.name_en)      IN ({name_ph})
             OR lower(c.name_de)      IN ({name_ph})
             OR lower(c.printed_name) IN ({name_ph})
            ){extra_where}
            ORDER BY c.chaos_key
            """,
            lower * 3 + extra_params,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def list_cards(
        self,
        limit: int = 200,
        offset: int = 0,
        sort: str = "chaos",
        language: Optional[str] = None,
        container_id: Optional[int] = None,
    ) -> list[dict]:
        if sort not in _SORT_MAP:
            raise ValueError(f"Invalid sort key: {sort!r}")
        order = _SORT_MAP[sort]

        conditions, params = [], []
        if language:
            conditions.append("c.language = ?")
            params.append(language)
        if container_id == -1:
            conditions.append("c.container_id IS NULL")
        elif container_id is not None:
            conditions.append("c.container_id = ?")
            params.append(container_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params += [limit, offset]

        async with self._db.execute(
            f"""
            SELECT c.*, ct.name as container_name
            FROM collection_with_prices c
            LEFT JOIN containers ct ON c.container_id = ct.id
            {where} ORDER BY {order} LIMIT ? OFFSET ?
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]
