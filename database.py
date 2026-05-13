import aiosqlite
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sorting import compute_chaos_key, color_sort_order, type_sort_order

logger = logging.getLogger(__name__)

DB_PATH = "mtg_collection.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS containers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    type        TEXT NOT NULL DEFAULT 'binder',
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collection (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Scryfall identifiers
    scryfall_id         TEXT,
    oracle_id           TEXT,
    -- Names
    name_en             TEXT NOT NULL,
    name_de             TEXT,
    printed_name        TEXT,          -- name as it appears on the physical card
    -- Set / print details
    set_code            TEXT,
    set_name            TEXT,
    collector_number    TEXT,
    released_at         TEXT,
    -- Card properties
    rarity              TEXT,
    colors              TEXT,          -- JSON array  e.g. ["W","U"]
    color_identity      TEXT,          -- JSON array
    mana_cost           TEXT,
    cmc                 REAL DEFAULT 0,
    type_line           TEXT,
    oracle_text         TEXT,
    flavor_text         TEXT,
    power               TEXT,
    toughness           TEXT,
    loyalty             TEXT,
    keywords            TEXT,          -- JSON array
    legalities          TEXT,          -- JSON object
    -- Prices
    price_usd           REAL,
    price_eur           REAL,
    price_tix           REAL,
    -- Image
    image_url           TEXT,
    -- Collection metadata
    language            TEXT NOT NULL DEFAULT 'en',
    condition           TEXT NOT NULL DEFAULT 'NM',
    foil                INTEGER NOT NULL DEFAULT 0,
    quantity            INTEGER NOT NULL DEFAULT 1,
    notes               TEXT,
    added_by            TEXT,
    added_at            TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now')),
    -- Container (physical binder / box / deck)
    container_id        INTEGER REFERENCES containers(id) ON DELETE SET NULL,
    -- Chaos sort fields (precomputed)
    chaos_key           TEXT,
    color_order         INTEGER,
    type_order          INTEGER
);

-- Full-text search across every relevant text field
CREATE VIRTUAL TABLE IF NOT EXISTS collection_fts USING fts5(
    name_en,
    name_de,
    printed_name,
    set_code,
    set_name,
    collector_number,
    rarity,
    mana_cost,
    type_line,
    oracle_text,
    flavor_text,
    keywords,
    notes,
    content='collection',
    content_rowid='id'
);

-- Keep FTS in sync
CREATE TRIGGER IF NOT EXISTS collection_ai AFTER INSERT ON collection BEGIN
    INSERT INTO collection_fts(rowid, name_en, name_de, printed_name,
        set_code, set_name, collector_number, rarity, mana_cost,
        type_line, oracle_text, flavor_text, keywords, notes)
    VALUES (new.id, new.name_en, new.name_de, new.printed_name,
        new.set_code, new.set_name, new.collector_number, new.rarity,
        new.mana_cost, new.type_line, new.oracle_text, new.flavor_text,
        new.keywords, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS collection_ad AFTER DELETE ON collection BEGIN
    INSERT INTO collection_fts(collection_fts, rowid, name_en, name_de,
        printed_name, set_code, set_name, collector_number, rarity, mana_cost,
        type_line, oracle_text, flavor_text, keywords, notes)
    VALUES ('delete', old.id, old.name_en, old.name_de, old.printed_name,
        old.set_code, old.set_name, old.collector_number, old.rarity,
        old.mana_cost, old.type_line, old.oracle_text, old.flavor_text,
        old.keywords, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS collection_au AFTER UPDATE ON collection BEGIN
    INSERT INTO collection_fts(collection_fts, rowid, name_en, name_de,
        printed_name, set_code, set_name, collector_number, rarity, mana_cost,
        type_line, oracle_text, flavor_text, keywords, notes)
    VALUES ('delete', old.id, old.name_en, old.name_de, old.printed_name,
        old.set_code, old.set_name, old.collector_number, old.rarity,
        old.mana_cost, old.type_line, old.oracle_text, old.flavor_text,
        old.keywords, old.notes);
    INSERT INTO collection_fts(rowid, name_en, name_de, printed_name,
        set_code, set_name, collector_number, rarity, mana_cost,
        type_line, oracle_text, flavor_text, keywords, notes)
    VALUES (new.id, new.name_en, new.name_de, new.printed_name,
        new.set_code, new.set_name, new.collector_number, new.rarity,
        new.mana_cost, new.type_line, new.oracle_text, new.flavor_text,
        new.keywords, new.notes);
END;

-- Perceptual hash index for visual card matching
CREATE TABLE IF NOT EXISTS card_hashes (
    scryfall_id      TEXT PRIMARY KEY,
    name_en          TEXT NOT NULL,
    set_code         TEXT,
    collector_number TEXT,
    lang             TEXT DEFAULT 'en',
    phash            TEXT NOT NULL,
    indexed_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_card_hashes_set ON card_hashes(set_code);

-- Key/value store for index metadata
CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _row_to_dict(row: aiosqlite.Row) -> dict:
    d = dict(row)
    for field in ("colors", "color_identity", "keywords", "legalities"):
        if d.get(field) and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                pass
    return d


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self):
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        logger.info("Database initialized at %s", self.path)

    async def _migrate(self):
        async with self._db.execute("PRAGMA table_info(collection)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "container_id" not in cols:
            await self._db.execute(
                "ALTER TABLE collection ADD COLUMN container_id INTEGER REFERENCES containers(id) ON DELETE SET NULL"
            )
            await self._db.commit()
            logger.info("Migrated: added container_id to collection")

        # Add normalized phash column for improved lighting-invariant matching
        async with self._db.execute("PRAGMA table_info(card_hashes)") as cur:
            hash_cols = {row[1] for row in await cur.fetchall()}
        if "phash_norm" not in hash_cols:
            await self._db.execute(
                "ALTER TABLE card_hashes ADD COLUMN phash_norm TEXT"
            )
            await self._db.commit()
            logger.info("Migrated: added phash_norm to card_hashes (run /index rebuild to populate)")

        # Each row must represent exactly one physical card (quantity → individual rows)
        async with self._db.execute(
            "SELECT * FROM collection WHERE quantity > 1"
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            for row in rows:
                d = dict(row)
                qty  = d["quantity"]
                rid  = d["id"]
                await self._db.execute("UPDATE collection SET quantity=1 WHERE id=?", (rid,))
                for _ in range(qty - 1):
                    await self._db.execute(
                        """
                        INSERT INTO collection (
                            scryfall_id, oracle_id, name_en, name_de, printed_name,
                            set_code, set_name, collector_number, released_at,
                            rarity, colors, color_identity, mana_cost, cmc,
                            type_line, oracle_text, flavor_text, power, toughness,
                            loyalty, keywords, legalities, price_usd, price_eur,
                            price_tix, image_url, language, condition, foil,
                            quantity, notes, added_by, chaos_key, color_order,
                            type_order, container_id
                        ) VALUES (
                            :scryfall_id, :oracle_id, :name_en, :name_de, :printed_name,
                            :set_code, :set_name, :collector_number, :released_at,
                            :rarity, :colors, :color_identity, :mana_cost, :cmc,
                            :type_line, :oracle_text, :flavor_text, :power, :toughness,
                            :loyalty, :keywords, :legalities, :price_usd, :price_eur,
                            :price_tix, :image_url, :language, :condition, :foil,
                            1, :notes, :added_by, :chaos_key, :color_order,
                            :type_order, :container_id
                        )
                        """,
                        {**d, "quantity": 1},
                    )
            await self._db.commit()
            logger.info("Migrated: split %d multi-copy row(s) into individual entries", len(rows))

    async def close(self):
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------ #
    # Write operations                                                      #
    # ------------------------------------------------------------------ #

    async def add_card(self, card: dict, added_by: str = "") -> int:
        colors = card.get("colors", [])
        type_line = card.get("type_line", "")
        cmc = card.get("cmc", 0) or 0

        chaos_key = compute_chaos_key(colors, type_line, cmc, card.get("name_en", ""))
        c_order = color_sort_order(colors, type_line)
        t_order = type_sort_order(type_line)

        async with self._db.execute(
            """
            INSERT INTO collection (
                scryfall_id, oracle_id, name_en, name_de, printed_name,
                set_code, set_name, collector_number, released_at,
                rarity, colors, color_identity, mana_cost, cmc,
                type_line, oracle_text, flavor_text, power, toughness, loyalty,
                keywords, legalities, price_usd, price_eur, price_tix,
                image_url, language, condition, foil, quantity, notes,
                added_by, chaos_key, color_order, type_order, container_id
            ) VALUES (
                :scryfall_id, :oracle_id, :name_en, :name_de, :printed_name,
                :set_code, :set_name, :collector_number, :released_at,
                :rarity, :colors, :color_identity, :mana_cost, :cmc,
                :type_line, :oracle_text, :flavor_text, :power, :toughness, :loyalty,
                :keywords, :legalities, :price_usd, :price_eur, :price_tix,
                :image_url, :language, :condition, :foil, :quantity, :notes,
                :added_by, :chaos_key, :color_order, :type_order, :container_id
            )
            """,
            {
                "scryfall_id": card.get("scryfall_id"),
                "oracle_id": card.get("oracle_id"),
                "name_en": card.get("name_en", ""),
                "name_de": card.get("name_de"),
                "printed_name": card.get("printed_name"),
                "set_code": card.get("set_code"),
                "set_name": card.get("set_name"),
                "collector_number": card.get("collector_number"),
                "released_at": card.get("released_at"),
                "rarity": card.get("rarity"),
                "colors": json.dumps(colors),
                "color_identity": json.dumps(card.get("color_identity", [])),
                "mana_cost": card.get("mana_cost"),
                "cmc": cmc,
                "type_line": type_line,
                "oracle_text": card.get("oracle_text"),
                "flavor_text": card.get("flavor_text"),
                "power": card.get("power"),
                "toughness": card.get("toughness"),
                "loyalty": card.get("loyalty"),
                "keywords": json.dumps(card.get("keywords", [])),
                "legalities": json.dumps(card.get("legalities", {})),
                "price_usd": card.get("price_usd"),
                "price_eur": card.get("price_eur"),
                "price_tix": card.get("price_tix"),
                "image_url": card.get("image_url"),
                "language": card.get("language", "en"),
                "condition": card.get("condition", "NM"),
                "foil": 1 if card.get("foil") else 0,
                "quantity": card.get("quantity", 1),
                "notes": card.get("notes"),
                "added_by": added_by,
                "chaos_key": chaos_key,
                "color_order": c_order,
                "type_order": t_order,
                "container_id": card.get("container_id"),
            },
        ) as cur:
            row_id = cur.lastrowid
        await self._db.commit()
        return row_id

    async def remove_card(self, card_id: int) -> bool:
        async with self._db.execute(
            "DELETE FROM collection WHERE id = ?", (card_id,)
        ) as cur:
            deleted = cur.rowcount > 0
        await self._db.commit()
        return deleted

    _UPDATABLE_FIELDS = frozenset({
        "condition", "foil", "notes", "language",
        "price_usd", "price_eur", "container_id",
    })

    async def update_card(self, card_id: int, field: str, value: Any) -> bool:
        if field not in self._UPDATABLE_FIELDS:
            return False
        # field is a verified member of a frozenset of literal column names —
        # safe to interpolate; parameterised queries cannot bind column identifiers.
        col = field  # noqa: S608 — allowlist-validated above
        now = datetime.now(timezone.utc).isoformat()
        async with self._db.execute(
            f"UPDATE collection SET {col} = ?, updated_at = ? WHERE id = ?",
            (value, now, card_id),
        ) as cur:
            updated = cur.rowcount > 0
        await self._db.commit()
        return updated

    # ------------------------------------------------------------------ #
    # Read operations                                                       #
    # ------------------------------------------------------------------ #

    async def get_card(self, card_id: int) -> Optional[dict]:
        async with self._db.execute(
            """
            SELECT c.*, ct.name as container_name
            FROM collection c
            LEFT JOIN containers ct ON c.container_id = ct.id
            WHERE c.id = ?
            """,
            (card_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def search(self, query: str, limit: int = 20, offset: int = 0) -> list[dict]:
        async with self._db.execute(
            """
            SELECT c.*, ct.name as container_name
            FROM collection c
            LEFT JOIN containers ct ON c.container_id = ct.id
            JOIN collection_fts fts ON c.id = fts.rowid
            WHERE collection_fts MATCH ?
            ORDER BY c.chaos_key
            LIMIT ? OFFSET ?
            """,
            (query, limit, offset),
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
        order = {
            "chaos": "c.chaos_key",
            "name": "c.name_en",
            "set": "c.set_code, c.collector_number",
            "cmc": "c.cmc, c.name_en",
            "added": "c.added_at DESC",
        }.get(sort, "c.chaos_key")

        conditions, params = [], []
        if language:
            conditions.append("c.language = ?")
            params.append(language)
        if container_id is not None:
            conditions.append("c.container_id = ?")
            params.append(container_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params += [limit, offset]

        async with self._db.execute(
            f"""
            SELECT c.*, ct.name as container_name
            FROM collection c
            LEFT JOIN containers ct ON c.container_id = ct.id
            {where} ORDER BY {order} LIMIT ? OFFSET ?
            """,
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

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
            FROM collection
            """
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {}
        result = dict(row)

        # Top 5 cards by EUR value
        async with self._db.execute(
            """
            SELECT c.name_en, c.name_de, c.price_eur, c.foil, c.language,
                   ct.name AS container_name
            FROM collection c
            LEFT JOIN containers ct ON c.container_id = ct.id
            WHERE c.price_eur IS NOT NULL
            ORDER BY c.price_eur DESC
            LIMIT 5
            """
        ) as cur:
            result["top_cards"] = [dict(r) for r in await cur.fetchall()]

        return result

    async def get_all(self) -> list[dict]:
        async with self._db.execute(
            """
            SELECT c.*, ct.name as container_name
            FROM collection c
            LEFT JOIN containers ct ON c.container_id = ct.id
            ORDER BY c.chaos_key
            """
        ) as cur:
            rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Container operations                                                  #
    # ------------------------------------------------------------------ #

    async def create_container(self, name: str, description: str = "", type: str = "binder") -> int:
        async with self._db.execute(
            "INSERT INTO containers (name, description, type) VALUES (?, ?, ?)",
            (name, description or None, type),
        ) as cur:
            row_id = cur.lastrowid
        await self._db.commit()
        return row_id

    async def container_stats(self) -> list[dict]:
        """Per-container card count, total value, and max single-card value (for bulk detection)."""
        async with self._db.execute(
            """
            SELECT
                ct.id,
                ct.name,
                ct.type,
                COUNT(c.id)                                        AS card_count,
                ROUND(SUM(COALESCE(c.price_eur, 0)), 2)            AS total_value_eur,
                MAX(COALESCE(c.price_eur, 0))                      AS max_card_eur
            FROM containers ct
            LEFT JOIN collection c ON c.container_id = ct.id
            GROUP BY ct.id, ct.name, ct.type
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
                ROUND(SUM(COALESCE(c.price_eur, 0)), 2) as total_value_eur
            FROM containers ct
            LEFT JOIN collection c ON c.container_id = ct.id
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

    async def delete_container(self, container_id: int) -> bool:
        async with self._db.execute(
            "DELETE FROM containers WHERE id = ?", (container_id,)
        ) as cur:
            deleted = cur.rowcount > 0
        await self._db.commit()
        return deleted

    async def rename_container(self, container_id: int, new_name: str) -> bool:
        async with self._db.execute(
            "UPDATE containers SET name = ? WHERE id = ?", (new_name, container_id)
        ) as cur:
            updated = cur.rowcount > 0
        await self._db.commit()
        return updated

    # ------------------------------------------------------------------ #
    # Hash index operations                                                 #
    # ------------------------------------------------------------------ #

    async def upsert_card_hash(
        self,
        scryfall_id: str,
        name_en: str,
        set_code: str,
        collector_number: str,
        lang: str,
        phash: str,
        phash_norm: Optional[str] = None,
    ):
        await self._db.execute(
            """
            INSERT INTO card_hashes
                (scryfall_id, name_en, set_code, collector_number, lang, phash, phash_norm)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scryfall_id) DO UPDATE SET
                phash=excluded.phash,
                phash_norm=excluded.phash_norm,
                indexed_at=datetime('now')
            """,
            (scryfall_id, name_en, set_code, collector_number, lang, phash, phash_norm),
        )
        await self._db.commit()

    async def get_all_hashes(self) -> list[dict]:
        async with self._db.execute(
            """
            SELECT scryfall_id, name_en, set_code, collector_number,
                   lang, phash, phash_norm
            FROM card_hashes
            """
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_indexed_scryfall_ids(self) -> set:
        async with self._db.execute("SELECT scryfall_id FROM card_hashes") as cur:
            rows = await cur.fetchall()
        return {r[0] for r in rows}

    async def get_indexed_set_codes(self) -> set:
        async with self._db.execute("SELECT DISTINCT set_code FROM card_hashes") as cur:
            rows = await cur.fetchall()
        return {r[0] for r in rows if r[0]}

    async def get_hash_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM card_hashes") as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    async def clear_card_hashes(self):
        await self._db.execute("DELETE FROM card_hashes")
        await self._db.execute("DELETE FROM index_meta WHERE key = 'last_built_at'")
        await self._db.commit()

    async def get_index_meta(self, key: str) -> Optional[str]:
        async with self._db.execute(
            "SELECT value FROM index_meta WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def set_index_meta(self, key: str, value: str):
        await self._db.execute(
            "INSERT INTO index_meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self._db.commit()
