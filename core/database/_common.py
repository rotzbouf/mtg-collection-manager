"""Shared constants and utilities used across database mixins."""
import json
import aiosqlite

from core.config import DATA_DIR as _DATA_DIR

DB_PATH = str(_DATA_DIR / "db" / "mtg_collection.db")

_SORT_MAP = {
    "chaos": "c.chaos_key",
    "name":  "c.name_en",
    "set":   "c.set_code, c.collector_number",
    "cmc":   "c.cmc, c.name_en",
    "added": "c.added_at DESC",
}

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
    cardmarket_id       INTEGER,
    -- Names
    name_en             TEXT NOT NULL,
    name_de             TEXT,
    printed_name        TEXT,
    -- Set / print details
    set_code            TEXT,
    set_name            TEXT,
    collector_number    TEXT,
    released_at         TEXT,
    -- Card properties
    rarity              TEXT,
    colors              TEXT,
    color_identity      TEXT,
    mana_cost           TEXT,
    cmc                 REAL DEFAULT 0,
    type_line           TEXT,
    oracle_text         TEXT,
    flavor_text         TEXT,
    power               TEXT,
    toughness           TEXT,
    loyalty             TEXT,
    keywords            TEXT,
    legalities          TEXT,
    -- Prices
    price_usd           REAL,
    price_eur           REAL,
    price_tix           REAL,
    -- Image
    image_url           TEXT,
    image_url_back      TEXT,
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
    name_en, name_de, printed_name, set_code, set_name, collector_number,
    rarity, mana_cost, type_line, oracle_text, flavor_text, keywords, notes,
    content='collection', content_rowid='id'
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

-- Daily price snapshots for price-history charts
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scryfall_id TEXT NOT NULL,
    price_eur   REAL NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (date('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_history_unique
    ON price_history(scryfall_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_price_history_lookup
    ON price_history(scryfall_id);

-- Format ban tracking (derived from Scryfall legalities, rebuilt on startup)
CREATE TABLE IF NOT EXISTS format_bans (
    format       TEXT NOT NULL,
    card_name    TEXT NOT NULL,
    status       TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (format, card_name)
);

-- Manual overrides: status='legal' un-bans a card, 'banned'/'restricted' force-bans one
CREATE TABLE IF NOT EXISTS format_ban_overrides (
    format    TEXT NOT NULL,
    card_name TEXT NOT NULL,
    status    TEXT NOT NULL,
    reason    TEXT,
    added_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (format, card_name)
);

-- Cardmarket price cache (downloaded from CM bulk data, refreshed manually or daily)
CREATE TABLE IF NOT EXISTS cm_prices (
    cm_id       INTEGER PRIMARY KEY,
    low         REAL,
    trend       REAL,
    avg7        REAL,
    avg30       REAL,
    foil_low    REAL,
    foil_trend  REAL,
    foil_avg30  REAL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- ── Meta / competitive data ────────────────────────────────────────────────
-- Decks scraped from online competitive databases (currently mtgtop8.com)
CREATE TABLE IF NOT EXISTS meta_decks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL DEFAULT 'mtgtop8',
    format       TEXT NOT NULL,
    event_id     TEXT NOT NULL,
    deck_id      TEXT NOT NULL,
    player       TEXT,
    place        TEXT,
    crawled_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, event_id, deck_id)
);

-- Individual cards within each meta deck (mainboard and sideboard)
CREATE TABLE IF NOT EXISTS meta_deck_cards (
    deck_db_id   INTEGER NOT NULL REFERENCES meta_decks(id) ON DELETE CASCADE,
    card_name    TEXT NOT NULL,
    quantity     INTEGER NOT NULL DEFAULT 1,
    section      TEXT NOT NULL DEFAULT 'main',
    PRIMARY KEY (deck_db_id, card_name, section)
);

-- Per-format card scores derived from meta_deck_cards (recomputed after each crawl)
CREATE TABLE IF NOT EXISTS meta_card_scores (
    card_name    TEXT NOT NULL,
    format       TEXT NOT NULL,
    score        REAL NOT NULL DEFAULT 0,
    appearances  INTEGER NOT NULL DEFAULT 0,
    deck_count   INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (card_name, format)
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
