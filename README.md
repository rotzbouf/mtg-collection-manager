# MTG Collection Manager

A desktop application for tracking your physical Magic: The Gathering collection.
Manage cards, containers, and decks through a native PyQt6 interface — with an optional server mode
that adds a Discord bot and browser-based web UI sharing the same database.

---

## Quick Start

```bash
git clone https://github.com/rotzbouf/mtg-collection-manager.git
cd mtg-collection-manager
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
./start_desktop.sh
```

The database (`mtg_collection.db`) is created automatically on first launch.

### Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | |
| PyQt6, qasync | Desktop GUI |
| aiohttp, aiosqlite | Async networking and database |
| Scryfall API | Card data and prices (no key required) |

Full dependency list in `requirements.txt`.

---

## Desktop App

Launch with `./start_desktop.sh`. Requires a display (X11 or Wayland).

### Tabs

| Tab | What it offers |
|---|---|
| **Collection** | Paginated card list; text search + ID search; multi-select with context menu to move/remove; edit, resync, price history, delete per card |
| **Add Card** | Name lookup with set / condition / foil / language / container picker; confirms with collection ID and current container fill count |
| **Search** | Full-text search across all fields; multi-select rows; context menu to move or remove cards |
| **Containers** | Browse cards per container; multi-select context menu for move/remove; single-card actions: commander toggle, resync, price history |
| **Overcount** | Cards exceeding a configurable copy threshold; split view with card detail panel; multi-select move/remove |
| **Stats** | Totals, rarity breakdown, language split, top-5 by value |
| **Import / Export** | Moxfield CSV, full CSV, JSON import and export; backup create/restore with confirmation dialog |
| **Settings** | Environment variables, container types, overcount exclusions, backup directory with folder picker |

---

## Features

| Category | What it does |
|---|---|
| **Add by name** | Resolves English or German card names via Scryfall; auto-detects language |
| **Containers** | Organise cards into named binders, boxes, decks, trade piles, etc. |
| **Full-text search** | SQLite FTS5 across name, type, oracle text, set, flavour text, notes |
| **Chaos sort** | MTG-native sort: W→U→B→R→G→Multi→Colourless→Land, then type, then CMC |
| **Statistics** | Totals by language, foil/non-foil, rarity breakdown, top-5 by value |
| **Export** | Moxfield CSV (default), Excel CSV, or JSON |
| **Import** | Moxfield CSV, bot-export CSV, or bot-export JSON |
| **Deckbuilder** | Auto-generates Commander (100-card) or 60-card (Timeless/Standard) proposals; saves deck to a container |
| **Deck list** | Every proposed card shows its storage location; a location manifest records original container IDs |
| **Price history** | Prices are snapshotted daily; history chart appears once 2+ data points exist |
| **Null-price refresh** | Cards added without a EUR price are automatically re-checked against Scryfall daily |
| **Overcount** | Cards with more than N copies — configurable threshold; shown in the Overcount tab with detail panel and move/remove actions |
| **Backup & restore** | Save/restore `.db` or `.db.xz`; confirmation dialog with card and container counts |
| **Local image cache** | Card images downloaded at startup and served locally |
| **Resync** | Re-fetches fresh Scryfall data (text, prices, image) for one or all cards |

---

## Architecture

```
core/               — Shared service layer (used by all interfaces)
│   database.py     — Async SQLite via aiosqlite; schema, migrations, all queries
│   scryfall.py     — Scryfall API client with rate limiting and 429 retry backoff
│   scanner.py      — Card isolation (OpenCV), OCR (EasyOCR CPU → pytesseract fallback)
│   image_cache.py  — Local card image cache (images/<scryfall_id>.<ext>)
│   sorting.py      — Chaos sort key computation
│   deckbuilder.py  — Synergy scoring and deck construction
│   exporter.py     — Moxfield CSV, full CSV, and JSON serialisation
│   importer.py     — Moxfield CSV, full CSV, and JSON parsing

desktop/            — Native desktop app (PyQt6 + qasync)
│   app.py          — QApplication entry point
│   main_window.py  — Sidebar navigation + stacked pages, DB initialisation
│   db.py           — Shared Database + ScryfallClient instances
│   widgets/        — Collection, Containers, Stats, ImportExport, Deck pages
│   dialogs/        — Add card, edit card, container dialogs

cogs/               — Discord bot feature modules (discord.py Cogs)
server/             — Server-only code and scripts
│   ui/             — Web UI (FastAPI + Jinja2 + HTMX)
│   install.sh      — Dependency setup for server deployment
│   service_install.sh / service_uninstall.sh — systemd service management
│   start_ui.sh     — Launch the web UI
```

### Database schema

| Table | Purpose |
|---|---|
| `collection` | One row per physical card |
| `containers` | Named storage locations |
| `collection_fts` | FTS5 virtual table, auto-synced via triggers |
| `price_history` | Daily EUR price snapshots per scryfall_id |

The database is created automatically on first run at `./mtg_collection.db`. Schema migrations run on startup. WAL mode is enabled for safe concurrent access.

---

## Server Mode (Discord Bot + Web UI)

The collection can also be run on a server with a Discord bot for remote card scanning
and a local web UI accessible via browser. Both share the same SQLite database as the desktop app.

### Setup

```bash
bash server/install.sh          # install dependencies and create .env
nano .env                       # fill in DISCORD_TOKEN and channel IDs
sudo bash server/service_install.sh   # register and start the systemd service
```

```bash
sudo systemctl status mtg-bot
sudo journalctl -u mtg-bot -f
sudo bash server/service_uninstall.sh   # remove the service
```

**Updating:**
```bash
git pull && sudo systemctl restart mtg-bot
```

**Web UI** (runs alongside the bot):
```bash
./server/start_ui.sh    # opens http://localhost:8000
```

### Configuration (`.env`)

```dotenv
# Required
DISCORD_TOKEN=your_discord_bot_token_here

# Optional — restrict slash command sync to one guild (faster for development)
DISCORD_GUILD_ID=

# Channels (leave blank = any channel)
DISCORD_SCAN_CHANNEL_ID=
DISCORD_DECKBUILDER_CHANNEL_ID=
DISCORD_SHOWCASE_CHANNEL_ID=
DISCORD_SEARCH_CHANNEL_ID=

# Server-side backup directory
BACKUP_DIR=backups

# Role-based access control (role name or ID; leave blank = everyone)
DISCORD_GUEST_ROLE=
DISCORD_COLLECTOR_ROLE=
DISCORD_ADMIN_ROLE=

# Local image cache directory (default: ./images)
IMAGE_CACHE_DIR=images

# Web UI host and port (default: localhost:8000)
UI_HOST=127.0.0.1
UI_PORT=8000

# Set to 1 to receive a debug image after each scan showing the OCR name zone
DEBUG_SCAN_PREVIEW=0
```

### Discord Bot Permissions

**OAuth2 invite permission integer:** `117760`

| Permission | Why |
|---|---|
| View Channel | See channels and incoming messages |
| Send Messages | Post scan status, command responses, error messages |
| Embed Links | Send card confirmations, search results, stats, showcase |
| Attach Files | Send deck `.txt`, export CSV/JSON, backup `.db.xz`, charts |
| Read Message History | Required for showcase channel welcome embed |

Two **privileged gateway intents** must be enabled in the Discord Developer Portal:
- **Message Content Intent** — read image attachments in the scan channel
- **Server Members Intent** — resolve display names in scan confirmations

### Role-based Access

```
Admin  ≥  Collector  ≥  Guest
```

| Variable | Permissions |
|---|---|
| `DISCORD_GUEST_ROLE` | `/list`, `/search`, `/stats`, `/export`, `/browse`, `/deck propose`, `/showcase` |
| `DISCORD_COLLECTOR_ROLE` | `/add`, `/import`, Browse → Edit/Move/Resync card + all Guest |
| `DISCORD_ADMIN_ROLE` | Browse → Delete, Rename container, `/resync`, `/backup` + all Collector |

### Discord Commands

**Adding cards**

| Command | Description |
|---|---|
| `/add name: [set_code:] [condition:] [foil:] [quantity:] [notes:]` | Add a card by name |
| Drop image in scan channel | Auto-scan: OCR reads name, set, collector number, language; container picker on first scan |

**Viewing**

| Command | Description |
|---|---|
| `/list [sort:] [language:]` | Browse collection (paginated, 10 per page) |
| `/search query:` | Full-text search across all fields |
| `/browse` | Interactive container and card browser |
| `/stats` | Totals, rarity breakdown, top-5 by value, per-container overview |
| `/showcase` | 5 most valuable cards with image, price, and price-history chart |

**Containers**

| Command | Description |
|---|---|
| `/container list` | All containers with card count and EUR value |
| `/container move source: destination:` | Move all cards between containers |

**Import / Export / Backup**

| Command | Description |
|---|---|
| `/export [format:]` | Moxfield CSV (default), full CSV, or JSON |
| `/import` | Moxfield CSV, bot-export CSV, or bot-export JSON |
| `/backup create` | Save server copy + send `.db.xz` attachment |
| `/backup restore` | Attach `.db` / `.db.gz` / `.db.xz` to restore |

**Deckbuilder**

| Command | Description |
|---|---|
| `/deck propose [format:] [commander:]` | Commander (100-card) or 60-card deck from your collection; **📦 Save to Container** button included |

**Resync**

| Command | Description |
|---|---|
| `/resync [id:]` | Re-fetch Scryfall data; omit `id` to resync all cards |
