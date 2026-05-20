# MTG Collection Manager

A desktop application for tracking your physical Magic: The Gathering collection.
Manage cards, containers, and decks through a native PyQt6 interface — with an optional server mode
that adds a Discord bot and a browser-based web UI sharing the same database.

---

## Screenshots

### Add & Scan
Drop in a card name or scan a physical card with your webcam — the app resolves it via Scryfall and adds it to the right container.

![Add / Scan](docs/screenshots/01_add_scan.png)

### Collection
Browse all your cards across Collection, Containers, and Overcount in a single tab group. Click any row to see card details on the right.

![Collection](docs/screenshots/02_collection.png)

### Deck Builder & Analysis
Build a Commander or 60-card deck straight from your collection, or analyse an existing one for mana curve and synergies.

![Decks](docs/screenshots/03_decks.png)

### Advanced Search
Filter by name, type line, oracle text, set, colours, rarity, CMC, condition, language, foil status, and container — all at once.

![Search](docs/screenshots/04_search.png)

### Statistics
At-a-glance overview: total value, rarity and language breakdown, foil split, and your top 5 most valuable cards with cover art.

![Statistics](docs/screenshots/05_statistics.png)

---

## Quick Start

```bash
git clone https://github.com/rotzbouf/mtg-collection-manager.git
cd mtg-collection-manager
bash install.sh          # sets up venv, system deps, and config.json
bash start_desktop.sh
```

The database (`mtg_collection.db`) is created automatically on first launch.

> **First scan:** EasyOCR downloads its language models (~150 MB) on the very first card scan. This happens once and is cached automatically.

### Requirements

| | |
|---|---|
| Python 3.10+ | |
| PyQt6, qasync | Desktop GUI |
| aiohttp, aiosqlite | Async networking and database |
| EasyOCR, OpenCV | Card scanning (CPU-only) |
| Scryfall API | Card data and prices — no key required |

Full dependency list in `requirements.txt`. Run `bash install.sh` to handle everything automatically.

---

## Desktop App

Launch with `bash start_desktop.sh`. Requires a display (X11 or Wayland).

### Navigation

| Section | What it contains |
|---|---|
| **Add / Scan** | Add Card (name / set / collector number lookup) · Scanner (webcam OCR) |
| **Collection** | Collection browser · Containers · Overcount |
| **Decks** | Deck Builder · Deck Analysis |
| **Search** | Full-text + multi-filter search across all fields |
| **Statistics** | Totals, rarity & language breakdown, top-5 by value |
| **Logs** | Live log stream with level filter |
| **Settings** | Config, container types, services (Discord bot / Web UI), backup / restore |

### Collection

Paginated card list with text search and ID lookup. Multi-select rows for batch move or remove. Click any card to see full details and price history in the side panel. Edit, resync from Scryfall, or delete individual cards.

### Containers

Organise cards into named binders, boxes, decks, trade piles, etc. Browse cards per container; multi-select context menu for move / remove. Toggle commander status on individual cards.

### Overcount

Three sub-tabs:
- **Overcounted** — cards exceeding a configurable copy threshold, with detail panel and move / remove actions
- **Sell Candidates** — cards in overcount containers above a price threshold, sorted by value
- **Bundle Builder** — create preset bundles (50/100 commons, 50/100 uncommons, all rares & mythics, by set) and save them as new box containers

### Deck Builder

Auto-generates a Commander (100-card EDH) or 60-card (Timeless / Standard) deck from your collection using synergy scoring. Every card shows its storage location. Save directly to a new container.

### Deck Analysis

Analyse any container flagged as a deck: mana curve chart, colour distribution, type breakdown, card list with CMC and container origin.

---

## Features

| Category | Details |
|---|---|
| **Add by name** | Resolves English or German card names via Scryfall; auto-detects language |
| **Scanner** | Webcam card isolation (OpenCV) + OCR (EasyOCR CPU / pytesseract fallback) |
| **Full-text search** | SQLite FTS5 across name, type, oracle text, set, flavour text, notes |
| **Chaos sort** | MTG-native colour order: W → U → B → R → G → Multi → Colourless → Land, then type, then CMC |
| **Price history** | Daily EUR snapshots per card; history chart appears once 2+ data points exist |
| **Null-price refresh** | Cards without a price are automatically re-checked against Scryfall daily |
| **Export** | Moxfield CSV (default), full CSV, or JSON |
| **Import** | Moxfield CSV, bot-export CSV, or bot-export JSON |
| **Backup & restore** | Save / restore `.db` or `.db.xz`; confirmation dialog with card and container counts |
| **Local image cache** | Card images downloaded from Scryfall and served locally |
| **Resync** | Re-fetches fresh Scryfall data (text, prices, image) for one or all cards |

---

## Configuration

Settings are stored in `config.json` (excluded from git — contains your Discord token).
On first run, `install.sh` creates it from `config.json.example`.
Open **Settings → Configuration** inside the app to fill in your values.

Key settings:

| Key | Default | Description |
|---|---|---|
| `discord.token` | — | Your Discord bot token |
| `app.price_source` | `scryfall` | Price provider (`scryfall` or `cardmarket`) |
| `app.ui_port` | `8080` | Web UI port |
| `container_types` | `["binder","box","deck","commander","overcount"]` | Available container types |
| `overcount_excluded_types` | `["deck","commander","overcount"]` | Types excluded from overcount checks |

---

## Architecture

```
core/               Shared service layer (used by all interfaces)
  database.py       Async SQLite via aiosqlite — schema, migrations, all queries
  scryfall.py       Scryfall API client with rate limiting and 429 backoff
  scanner.py        Card isolation (OpenCV) + OCR (EasyOCR CPU / pytesseract)
  image_cache.py    Local card image cache
  sorting.py        Chaos sort key computation
  deckbuilder.py    Synergy scoring and deck construction
  exporter.py       Moxfield CSV, full CSV, JSON serialisation
  importer.py       Moxfield CSV, full CSV, JSON parsing

desktop/            Native desktop app (PyQt6 + qasync)
  app.py            QApplication entry point
  main_window.py    Sidebar navigation + stacked pages, DB initialisation
  db.py             Shared Database + ScryfallClient singletons
  widgets/          All page widgets
  dialogs/          Card, container, price-history dialogs

server/             Server-only code
  bot.py            Discord bot (discord.py)
  ui/               Web UI (FastAPI + Jinja2 + HTMX)
  install.sh        Headless server setup
  *.service_install.sh  systemd service management

cogs/               Discord bot feature modules (discord.py Cogs)
```

### Database

| Table | Purpose |
|---|---|
| `collection` | One row per physical card |
| `containers` | Named storage locations |
| `collection_fts` | FTS5 virtual table, auto-synced via triggers |
| `price_history` | Daily EUR price snapshots per `scryfall_id` |

WAL mode enabled. Schema migrations run automatically on startup.

---

## Server Mode — Discord Bot + Web UI

The same database can be exposed via a Discord bot for remote scanning and a local web UI for browser access. Both share the desktop app's SQLite file.

### Setup

```bash
bash server/install.sh
# Fill in config.json (discord.token, channel IDs, etc.)
sudo bash server/mtg-discord-bot_service_install.sh
sudo bash server/mtg-webui_service_install.sh
```

```bash
sudo systemctl status mtg-discord-bot
sudo journalctl -u mtg-discord-bot -f
```

**Updating:**
```bash
git pull && sudo systemctl restart mtg-discord-bot
```

**Web UI:**
```bash
bash server/start_ui.sh    # http://localhost:8080
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

| Role | Permissions |
|---|---|
| Guest | `/list`, `/search`, `/stats`, `/export`, `/browse`, `/deck propose`, `/showcase` |
| Collector | `/add`, Browse → Edit / Move / Resync + all Guest |
| Admin | Browse → Delete, Rename container, `/resync`, `/backup` + all Collector |

### Discord Commands

**Adding cards**

| Command | Description |
|---|---|
| `/add name: [set_code:] [condition:] [foil:] [quantity:]` | Add a card by name |
| Drop image in scan channel | Auto-scan via OCR; container picker on first scan |

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
| `/deck propose [format:] [commander:]` | Commander (100-card) or 60-card deck from your collection |

**Resync**

| Command | Description |
|---|---|
| `/resync [id:]` | Re-fetch Scryfall data; omit `id` to resync all cards |
