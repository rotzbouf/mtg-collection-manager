"""Card CRUD — add, remove, update, resync, and bulk read operations."""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..sorting import compute_chaos_key, color_sort_order, type_sort_order
from ._common import _row_to_dict

logger = logging.getLogger(__name__)


class _CardsMixin:
    """Requires: self._db, self._write_lock."""

    _UPDATABLE_FIELDS = frozenset({
        "condition", "foil", "notes", "language",
        "price_usd", "price_eur", "container_id",
    })

    # ------------------------------------------------------------------ #
    # Write                                                                 #
    # ------------------------------------------------------------------ #

    async def add_card(self, card: dict, added_by: str = "") -> int:
        async with self._write_lock:
            return await self._add_card_locked(card, added_by)

    async def _add_card_locked(self, card: dict, added_by: str = "") -> int:
        colors    = card.get("colors", [])
        type_line = card.get("type_line", "")
        _cmc      = card.get("cmc", 0)
        try:
            cmc = float(_cmc) if _cmc is not None else 0.0
        except (TypeError, ValueError):
            cmc = 0.0

        chaos_key = compute_chaos_key(colors, type_line, cmc, card.get("name_en", ""))
        c_order   = color_sort_order(colors, type_line)
        t_order   = type_sort_order(type_line)

        async with self._db.execute(
            """
            INSERT INTO collection (
                scryfall_id, oracle_id, cardmarket_id, name_en, name_de, printed_name,
                set_code, set_name, collector_number, released_at,
                rarity, colors, color_identity, mana_cost, cmc,
                type_line, oracle_text, flavor_text, power, toughness, loyalty,
                keywords, legalities, price_usd, price_eur, price_tix,
                image_url, image_url_back, language, condition, foil, quantity, notes,
                added_by, chaos_key, color_order, type_order, container_id
            ) VALUES (
                :scryfall_id, :oracle_id, :cardmarket_id, :name_en, :name_de, :printed_name,
                :set_code, :set_name, :collector_number, :released_at,
                :rarity, :colors, :color_identity, :mana_cost, :cmc,
                :type_line, :oracle_text, :flavor_text, :power, :toughness, :loyalty,
                :keywords, :legalities, :price_usd, :price_eur, :price_tix,
                :image_url, :image_url_back, :language, :condition, :foil, :quantity, :notes,
                :added_by, :chaos_key, :color_order, :type_order, :container_id
            )
            """,
            {
                "scryfall_id":      card.get("scryfall_id"),
                "oracle_id":        card.get("oracle_id"),
                "cardmarket_id":    card.get("cardmarket_id"),
                "name_en":          card.get("name_en", ""),
                "name_de":          card.get("name_de"),
                "printed_name":     card.get("printed_name"),
                "set_code":         card.get("set_code"),
                "set_name":         card.get("set_name"),
                "collector_number": card.get("collector_number"),
                "released_at":      card.get("released_at"),
                "rarity":           card.get("rarity"),
                "colors":           json.dumps(colors),
                "color_identity":   json.dumps(card.get("color_identity", [])),
                "mana_cost":        card.get("mana_cost"),
                "cmc":              cmc,
                "type_line":        type_line,
                "oracle_text":      card.get("oracle_text"),
                "flavor_text":      card.get("flavor_text"),
                "power":            card.get("power"),
                "toughness":        card.get("toughness"),
                "loyalty":          card.get("loyalty"),
                "keywords":         json.dumps(card.get("keywords", [])),
                "legalities":       json.dumps(card.get("legalities", {})),
                "price_usd":        card.get("price_usd"),
                "price_eur":        card.get("price_eur"),
                "price_tix":        card.get("price_tix"),
                "image_url":        card.get("image_url"),
                "image_url_back":   card.get("image_url_back"),
                "language":         card.get("language", "en"),
                "condition":        card.get("condition", "NM"),
                "foil":             1 if card.get("foil") else 0,
                "quantity":         card.get("quantity", 1),
                "notes":            card.get("notes"),
                "added_by":         added_by,
                "chaos_key":        chaos_key,
                "color_order":      c_order,
                "type_order":       t_order,
                "container_id":     card.get("container_id"),
            },
        ) as cur:
            row_id = cur.lastrowid
        await self._db.commit()

        scryfall_id = card.get("scryfall_id")
        price_eur   = card.get("price_eur")
        price_usd   = card.get("price_usd")
        if scryfall_id:
            if price_eur is not None or price_usd is not None:
                await self._db.execute(
                    """
                    INSERT INTO card_prices (scryfall_id, price_eur, price_usd, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(scryfall_id) DO UPDATE SET
                        price_eur  = COALESCE(excluded.price_eur, price_eur),
                        price_usd  = COALESCE(excluded.price_usd, price_usd),
                        updated_at = datetime('now')
                    """,
                    (scryfall_id, price_eur, price_usd),
                )
            if price_eur is not None:
                await self._db.execute(
                    "INSERT OR IGNORE INTO price_history (scryfall_id, price_eur) VALUES (?, ?)",
                    (scryfall_id, price_eur),
                )
            await self._db.commit()

        return row_id

    async def remove_card(self, card_id: int) -> bool:
        async with self._write_lock:
            async with self._db.execute(
                "DELETE FROM collection WHERE id = ?", (card_id,)
            ) as cur:
                deleted = cur.rowcount > 0
            await self._db.commit()
            return deleted

    async def update_card(self, card_id: int, field: str, value: Any) -> bool:
        if field not in self._UPDATABLE_FIELDS:
            return False
        # field is a verified member of a frozenset of literal column names —
        # safe to interpolate; parameterised queries cannot bind column identifiers.
        col = field  # noqa: S608
        async with self._write_lock:
            now = datetime.now(timezone.utc).isoformat()
            async with self._db.execute(
                f"UPDATE collection SET {col} = ?, updated_at = ? WHERE id = ?",
                (value, now, card_id),
            ) as cur:
                updated = cur.rowcount > 0
            await self._db.commit()
            return updated

    async def resync_card(self, scryfall_id: str, card: dict) -> int:
        """Overwrite all Scryfall-sourced fields for every row with this scryfall_id.

        Preserves collection metadata (language, condition, foil, notes, container_id).
        Recomputes sort keys. Returns number of rows updated.
        """
        async with self._write_lock:
            return await self._resync_card_locked(scryfall_id, card)

    async def _resync_card_locked(self, scryfall_id: str, card: dict) -> int:
        colors    = card.get("colors", [])
        type_line = card.get("type_line", "")
        cmc       = card.get("cmc", 0) or 0
        chaos_key = compute_chaos_key(colors, type_line, cmc, card.get("name_en", ""))
        c_order   = color_sort_order(colors, type_line)
        t_order   = type_sort_order(type_line)

        result = await self._db.execute(
            """
            UPDATE collection SET
                name_en        = :name_en,
                name_de        = COALESCE(:name_de, name_de),
                printed_name   = COALESCE(:printed_name, printed_name),
                set_name       = :set_name,
                rarity         = :rarity,
                colors         = :colors,
                color_identity = :color_identity,
                mana_cost      = :mana_cost,
                cmc            = :cmc,
                type_line      = :type_line,
                oracle_text    = :oracle_text,
                flavor_text    = :flavor_text,
                power          = :power,
                toughness      = :toughness,
                loyalty        = :loyalty,
                keywords       = :keywords,
                legalities     = :legalities,
                price_usd      = COALESCE(:price_usd, price_usd),
                price_eur      = COALESCE(:price_eur, price_eur),
                image_url      = :image_url,
                image_url_back = COALESCE(:image_url_back, image_url_back),
                cardmarket_id  = COALESCE(:cardmarket_id, cardmarket_id),
                chaos_key      = :chaos_key,
                color_order    = :color_order,
                type_order     = :type_order,
                updated_at     = datetime('now')
            WHERE scryfall_id = :scryfall_id
            """,
            {
                "scryfall_id":    scryfall_id,
                "name_en":        card.get("name_en", ""),
                "name_de":        card.get("name_de"),
                "printed_name":   card.get("printed_name"),
                "set_name":       card.get("set_name"),
                "rarity":         card.get("rarity"),
                "colors":         json.dumps(colors),
                "color_identity": json.dumps(card.get("color_identity", [])),
                "mana_cost":      card.get("mana_cost"),
                "cmc":            cmc,
                "type_line":      type_line,
                "oracle_text":    card.get("oracle_text"),
                "flavor_text":    card.get("flavor_text"),
                "power":          card.get("power"),
                "toughness":      card.get("toughness"),
                "loyalty":        card.get("loyalty"),
                "keywords":       json.dumps(card.get("keywords", [])),
                "legalities":     json.dumps(card.get("legalities", {})),
                "price_usd":      card.get("price_usd"),
                "price_eur":      card.get("price_eur"),
                "image_url":      card.get("image_url"),
                "image_url_back": card.get("image_url_back"),
                "cardmarket_id":  card.get("cardmarket_id"),
                "chaos_key":      chaos_key,
                "color_order":    c_order,
                "type_order":     t_order,
            },
        )
        await self._db.commit()

        price_eur = card.get("price_eur")
        price_usd = card.get("price_usd")
        if price_eur is not None or price_usd is not None:
            await self._db.execute(
                """
                INSERT INTO card_prices (scryfall_id, price_eur, price_usd, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(scryfall_id) DO UPDATE SET
                    price_eur  = COALESCE(excluded.price_eur, price_eur),
                    price_usd  = COALESCE(excluded.price_usd, price_usd),
                    updated_at = datetime('now')
                """,
                (scryfall_id, price_eur, price_usd),
            )
        if price_eur is not None:
            await self._db.execute(
                "INSERT OR IGNORE INTO price_history (scryfall_id, price_eur) VALUES (?, ?)",
                (scryfall_id, price_eur),
            )
        await self._db.commit()
        return result.rowcount

    async def fix_card_lang_data(self, card_id: int, card_data: dict) -> None:
        """Overwrite the localised fields of a single collection row.

        Updates scryfall_id (in case the stored one was the EN edition),
        printed_name, name_de, oracle_text, flavor_text, and image_url.
        Existing values are preserved via COALESCE when the incoming value is None.
        """
        async with self._write_lock:
            await self._db.execute(
                """
                UPDATE collection SET
                    scryfall_id  = COALESCE(:scryfall_id, scryfall_id),
                    name_de      = COALESCE(:name_de, name_de),
                    printed_name = COALESCE(:printed_name, printed_name),
                    oracle_text  = COALESCE(:oracle_text, oracle_text),
                    flavor_text  = COALESCE(:flavor_text, flavor_text),
                    image_url    = COALESCE(:image_url, image_url),
                    updated_at   = datetime('now')
                WHERE id = :id
                """,
                {
                    "id":           card_id,
                    "scryfall_id":  card_data.get("scryfall_id"),
                    "name_de":      card_data.get("name_de") or card_data.get("printed_name"),
                    "printed_name": card_data.get("printed_name"),
                    "oracle_text":  card_data.get("oracle_text"),
                    "flavor_text":  card_data.get("flavor_text"),
                    "image_url":    card_data.get("image_url"),
                },
            )
            await self._db.commit()

    # ------------------------------------------------------------------ #
    # Read                                                                  #
    # ------------------------------------------------------------------ #

    async def get_card(self, card_id: int) -> Optional[dict]:
        async with self._db.execute(
            """
            SELECT c.*, ct.name as container_name
            FROM collection_with_prices c
            LEFT JOIN containers ct ON c.container_id = ct.id
            WHERE c.id = ?
            """,
            (card_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_dict(row) if row else None

    async def get_scryfall_ids_missing_data(self) -> list[str]:
        """Return scryfall_ids of cards missing oracle text, type line, or price."""
        async with self._db.execute(
            """
            SELECT DISTINCT c.scryfall_id FROM collection c
            LEFT JOIN card_prices cp ON c.scryfall_id = cp.scryfall_id
            WHERE c.scryfall_id IS NOT NULL
              AND (c.oracle_text IS NULL OR c.type_line IS NULL OR cp.price_eur IS NULL)
            """
        ) as cur:
            return [row[0] for row in await cur.fetchall()]

    async def get_cards_needing_lang_fix(self) -> list[dict]:
        """Cards where language != 'en' but printed_name is missing or identical to name_en."""
        async with self._db.execute(
            """
            SELECT id, scryfall_id, name_en, language, set_code, collector_number
            FROM collection
            WHERE language IS NOT NULL
              AND language != 'en'
              AND (printed_name IS NULL OR printed_name = '' OR printed_name = name_en)
            """
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_cards_needing_dfc_fix(self) -> list[dict]:
        """Double-faced cards missing mana_cost or back-face image URL."""
        async with self._db.execute(
            """
            SELECT DISTINCT scryfall_id, name_en
            FROM collection
            WHERE name_en LIKE '% // %'
              AND scryfall_id IS NOT NULL
              AND (mana_cost IS NULL OR image_url_back IS NULL)
            """
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_distinct_scryfall_ids(self) -> list[str]:
        """Return all distinct scryfall_ids present in the collection."""
        async with self._db.execute(
            "SELECT DISTINCT scryfall_id FROM collection WHERE scryfall_id IS NOT NULL"
        ) as cur:
            return [row[0] for row in await cur.fetchall()]

    async def get_recently_priced_ids(self) -> set[str]:
        """Return scryfall_ids whose price was updated today (UTC date)."""
        async with self._db.execute(
            "SELECT scryfall_id FROM card_prices WHERE date(updated_at) = date('now')"
        ) as cur:
            return {row[0] for row in await cur.fetchall()}

    # ------------------------------------------------------------------ #
    # Deck Improve                                                          #
    # ------------------------------------------------------------------ #

    async def get_deck_cards_for_improve(
        self, container_id: int, meta_format: str
    ) -> list[dict]:
        """Non-land, non-commander deck cards with their competitive meta score."""
        async with self._db.execute(
            """
            SELECT c.id, c.scryfall_id, c.name_en, c.printed_name, c.name_de,
                   c.type_line, c.cmc, c.color_identity,
                   c.image_url, c.image_url_back,
                   c.container_id, ct.name AS container_name,
                   COALESCE(mcs.score, 0)       AS meta_score,
                   COALESCE(mcs.appearances, 0) AS meta_appearances
            FROM collection c
            LEFT JOIN containers ct        ON c.container_id = ct.id
            LEFT JOIN meta_card_scores mcs ON lower(c.name_en) = mcs.card_name
                                          AND mcs.format = ?
            WHERE c.container_id = ?
              AND c.type_line NOT LIKE '%Land%'
              AND c.is_commander = 0
            ORDER BY meta_score ASC, c.cmc
            """,
            (meta_format, container_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_binder_cards_for_improve(
        self, meta_format: str, exclude_container_id: int
    ) -> list[dict]:
        """Cards from binder/box/unassigned containers with a competitive meta score.

        Excludes the deck being improved, and any other deck/commander containers.
        """
        async with self._db.execute(
            """
            SELECT c.id, c.scryfall_id, c.name_en, c.printed_name, c.name_de,
                   c.type_line, c.cmc, c.color_identity,
                   c.image_url, c.image_url_back,
                   c.container_id, ct.name AS container_name,
                   mcs.score       AS meta_score,
                   mcs.appearances AS meta_appearances
            FROM collection c
            JOIN  meta_card_scores mcs ON lower(c.name_en) = mcs.card_name
                                      AND mcs.format = ?
            LEFT JOIN containers ct    ON c.container_id = ct.id
            WHERE c.container_id != ?
              AND (ct.type IS NULL OR ct.type NOT IN ('commander', 'deck'))
              AND c.type_line NOT LIKE '%Land%'
            ORDER BY mcs.score DESC
            """,
            (meta_format, exclude_container_id),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def swap_card_containers(self, card_id_a: int, card_id_b: int) -> None:
        """Atomically swap the container_id of two collection rows."""
        async with self._write_lock:
            async with self._db.execute(
                "SELECT id, container_id FROM collection WHERE id IN (?, ?)",
                (card_id_a, card_id_b),
            ) as cur:
                rows = {row["id"]: row["container_id"] for row in await cur.fetchall()}

            cont_a = rows.get(card_id_a)
            cont_b = rows.get(card_id_b)
            now = datetime.now(timezone.utc).isoformat()

            await self._db.execute(
                "UPDATE collection SET container_id = ?, updated_at = ? WHERE id = ?",
                (cont_b, now, card_id_a),
            )
            await self._db.execute(
                "UPDATE collection SET container_id = ?, updated_at = ? WHERE id = ?",
                (cont_a, now, card_id_b),
            )
            await self._db.commit()

    async def get_all_image_refs(self) -> list[tuple[str, str]]:
        """Return unique (scryfall_id, image_url) pairs for all collection entries that have both."""
        async with self._db.execute(
            """
            SELECT DISTINCT scryfall_id, image_url
            FROM collection
            WHERE scryfall_id IS NOT NULL AND image_url IS NOT NULL
            """
        ) as cur:
            return [(r["scryfall_id"], r["image_url"]) for r in await cur.fetchall()]

    async def get_all(self, exclude_container_types: list[str] | None = None) -> list[dict]:
        if exclude_container_types:
            placeholders = ",".join("?" * len(exclude_container_types))
            sql = f"""
                SELECT c.*, ct.name as container_name, ct.type as container_type
                FROM collection_with_prices c
                LEFT JOIN containers ct ON c.container_id = ct.id
                WHERE ct.type IS NULL OR ct.type NOT IN ({placeholders})
                ORDER BY c.chaos_key
            """
            async with self._db.execute(sql, exclude_container_types) as cur:
                rows = await cur.fetchall()
        else:
            async with self._db.execute(
                """
                SELECT c.*, ct.name as container_name, ct.type as container_type
                FROM collection_with_prices c
                LEFT JOIN containers ct ON c.container_id = ct.id
                ORDER BY c.chaos_key
                """
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_dict(r) for r in rows]

    async def get_collection_languages(self) -> list[tuple[str, int]]:
        """Return [(language_code, card_count), ...] sorted by count descending.

        Only returns languages that actually appear in the collection.
        """
        async with self._db.execute(
            """
            SELECT language, COUNT(*) AS cnt
            FROM collection
            WHERE language IS NOT NULL AND language != ''
            GROUP BY language
            ORDER BY cnt DESC
            """
        ) as cur:
            return [(row[0], row[1]) for row in await cur.fetchall()]
