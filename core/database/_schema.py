"""Schema creation, migrations, and connection lifecycle."""
import logging

from ._common import _SCHEMA

logger = logging.getLogger(__name__)


class _SchemaMixin:
    """Requires: self._db, self.path."""

    async def initialize(self):
        import aiosqlite
        from pathlib import Path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        logger.info("Database initialized at %s", self.path)

    async def _migrate(self):
        # All DDL in a single transaction — if interrupted, nothing is half-applied.
        try:
            async with self._db.execute("PRAGMA table_info(collection)") as cur:
                cols = {row[1] for row in await cur.fetchall()}

            if "container_id" not in cols:
                await self._db.execute(
                    "ALTER TABLE collection ADD COLUMN container_id INTEGER REFERENCES containers(id) ON DELETE SET NULL"
                )
                logger.info("Migrated: added container_id to collection")

            if "is_commander" not in cols:
                await self._db.execute(
                    "ALTER TABLE collection ADD COLUMN is_commander INTEGER DEFAULT 0"
                )
                logger.info("Migrated: added is_commander to collection")

            async with self._db.execute("PRAGMA table_info(containers)") as cur:
                cont_cols = {row[1] for row in await cur.fetchall()}

            if "deck_format" not in cont_cols:
                await self._db.execute("ALTER TABLE containers ADD COLUMN deck_format TEXT")
                await self._db.execute(
                    "UPDATE containers SET deck_format = 'commander' WHERE type = 'commander'"
                )
                logger.info("Migrated: added deck_format to containers")

            if "color_identity" not in cont_cols:
                await self._db.execute("ALTER TABLE containers ADD COLUMN color_identity TEXT")
                logger.info("Migrated: added color_identity to containers")

            # Normalised price table — one row per scryfall_id
            async with self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='card_prices'"
            ) as cur:
                has_price_table = (await cur.fetchone()) is not None

            if not has_price_table:
                await self._db.execute("""
                    CREATE TABLE card_prices (
                        scryfall_id TEXT PRIMARY KEY,
                        price_eur   REAL,
                        price_usd   REAL,
                        updated_at  TEXT DEFAULT (datetime('now'))
                    )
                """)
                await self._db.execute("""
                    INSERT OR IGNORE INTO card_prices (scryfall_id, price_eur, price_usd)
                    SELECT scryfall_id, MAX(price_eur), MAX(price_usd)
                    FROM collection
                    WHERE scryfall_id IS NOT NULL
                    GROUP BY scryfall_id
                """)
                logger.info("Migrated: created card_prices table")

            if "cardmarket_id" not in cols:
                await self._db.execute(
                    "ALTER TABLE collection ADD COLUMN cardmarket_id INTEGER"
                )
                logger.info("Migrated: added cardmarket_id to collection")

            if "image_url_back" not in cols:
                await self._db.execute(
                    "ALTER TABLE collection ADD COLUMN image_url_back TEXT"
                )
                logger.info("Migrated: added image_url_back to collection")

            async with self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cm_prices'"
            ) as cur:
                has_cm_prices = (await cur.fetchone()) is not None

            if not has_cm_prices:
                await self._db.execute("""
                    CREATE TABLE cm_prices (
                        cm_id       INTEGER PRIMARY KEY,
                        low         REAL,
                        trend       REAL,
                        avg7        REAL,
                        avg30       REAL,
                        foil_low    REAL,
                        foil_trend  REAL,
                        foil_avg30  REAL,
                        updated_at  TEXT DEFAULT (datetime('now'))
                    )
                """)
                logger.info("Migrated: created cm_prices table")

            # Recreate view every startup so it stays in sync with column additions.
            await self._db.execute("DROP VIEW IF EXISTS collection_with_prices")
            await self._db.execute("""
                CREATE VIEW collection_with_prices AS
                SELECT
                    c.id, c.scryfall_id, c.oracle_id, c.cardmarket_id,
                    c.name_en, c.name_de, c.printed_name,
                    c.set_code, c.set_name, c.collector_number, c.released_at,
                    c.rarity, c.colors, c.color_identity, c.mana_cost, c.cmc,
                    c.type_line, c.oracle_text, c.flavor_text,
                    c.power, c.toughness, c.loyalty, c.keywords, c.legalities,
                    COALESCE(cp.price_eur, c.price_eur) AS price_eur,
                    COALESCE(cp.price_usd, c.price_usd) AS price_usd,
                    c.price_tix, c.image_url, c.image_url_back,
                    c.language, c.condition, c.foil, c.quantity, c.notes,
                    c.added_by, c.added_at, c.updated_at,
                    c.container_id, c.is_commander,
                    c.chaos_key, c.color_order, c.type_order,
                    cmp.trend AS cm_trend,
                    cmp.avg30 AS cm_avg30,
                    cmp.foil_trend AS cm_foil_trend
                FROM collection c
                LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
                LEFT JOIN cm_prices cmp ON c.cardmarket_id = cmp.cm_id
            """)

            # Enforce one row per physical card (quantity → individual rows)
            async with self._db.execute(
                "SELECT * FROM collection WHERE quantity > 1"
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                for row in rows:
                    d = dict(row)
                    qty = d["quantity"]
                    rid = d["id"]
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
                                price_tix, image_url, image_url_back, language, condition, foil,
                                quantity, notes, added_by, chaos_key, color_order,
                                type_order, container_id
                            ) VALUES (
                                :scryfall_id, :oracle_id, :name_en, :name_de, :printed_name,
                                :set_code, :set_name, :collector_number, :released_at,
                                :rarity, :colors, :color_identity, :mana_cost, :cmc,
                                :type_line, :oracle_text, :flavor_text, :power, :toughness,
                                :loyalty, :keywords, :legalities, :price_usd, :price_eur,
                                :price_tix, :image_url, :image_url_back, :language, :condition, :foil,
                                1, :notes, :added_by, :chaos_key, :color_order,
                                :type_order, :container_id
                            )
                            """,
                            {**d, "quantity": 1},
                        )
                logger.info("Migrated: split %d multi-copy row(s) into individual entries", len(rows))

            # Single commit for all DDL/DML above.
            await self._db.commit()

        except Exception as exc:
            logger.error("Migration failed, rolling back: %s", exc)
            try:
                await self._db.rollback()
            except Exception:
                pass
            raise

    async def close(self):
        if self._db:
            await self._db.close()

    async def commit(self):
        await self._db.commit()
