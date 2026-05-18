# MTG Collection Manager

A multi-interface tool for tracking your physical Magic: The Gathering collection.
Manage cards via Discord slash commands, a local web UI, or a standalone desktop app —
all sharing the same SQLite database.

---

## Interfaces

| Interface | How to run | Best for |
|---|---|---|
| **Discord bot** | `python3 bot.py` or systemd service | Scanning, adding by photo, remote access |
| **Web UI** | `./start_ui.sh` | Browser-based collection management |
| **Desktop app** | `./start_desktop.sh` | Native GUI without a browser |

All three interfaces read and write the same `mtg_collection.db` (SQLite WAL mode, safe for concurrent access).

---

## Features

| Category | What it does |
|---|---|
| **Add by name** | Resolves English or German card names via Scryfall; auto-detects language |
| **Add by photo** | Drop an image in the scan channel; OCR reads card name, set code, collector number, and language |
| **Container memory** | After picking a container once, subsequent scans go straight to confirmation — no repeated selection |
| **Localised card text & names** | Type line, oracle text, flavour text, and display names are stored in the card's own language |
| **Containers** | Organise cards into named binders, boxes, decks, trade piles, etc. |
| **Full-text search** | SQLite FTS5 across name, type, oracle text, set, flavour text, notes — paginated with card picker |
| **Interactive browsing** | Browse containers and cards; edit, move, resync, or delete cards; manage containers |
| **Chaos sort** | MTG-native sort: W→U→B→R→G→Multi→Colourless→Land, then type, then CMC |
| **Statistics** | Totals by language, foil/non-foil, rarity breakdown, top-5 by value |
| **Export** | Moxfield CSV (default), Excel CSV, or JSON |
| **Import** | Moxfield CSV, bot-export CSV, or bot-export JSON |
| **Deckbuilder** | Auto-generates Commander (100-card) or 60-card (Timeless/Standard) proposals; saves deck to a container |
| **Deck list** | Every proposed card shows its storage location; a location manifest at the end records original container IDs |
| **Showcase** | Displays the 5 most valuable cards with image, details, and a price-history chart |
| **Price history** | Prices are snapshotted daily; history chart auto-appears once 2+ data points exist |
| **Null-price refresh** | Cards added without a EUR price are automatically re-checked against Scryfall daily |
| **Overcount** | Cards with more than N copies — configurable threshold; shown via the Overcounted Cards button in `/stats` and in the desktop Overcount tab with card detail panel and move/remove actions |
| **Backup & restore** | `/backup create` saves a server copy and sends a `.db.xz` attachment; web UI and desktop app also support create/restore with a confirmation dialog |
| **Local image cache** | Card images downloaded at startup and served locally; no repeated Scryfall hits |
| **Resync** | Re-fetches fresh Scryfall data (text, prices, image) for one or all cards |

---

## Requirements

### System

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10 or newer | |
| tesseract-ocr | any recent | OCR fallback |
| tesseract-ocr-deu | — | German language pack |
| libgl1 | — | Required by OpenCV |
| libglib2.0-0 | — | Required by OpenCV |

### Python packages

All declared in `requirements.txt`:

```
discord.py>=2.3.0
aiohttp>=3.9.0
aiosqlite>=0.19.0
python-dotenv>=1.0.0
Pillow>=10.0.0
pytesseract>=0.3.10
easyocr>=1.7.0
numpy>=1.24.0
opencv-python-headless>=4.8.0
matplotlib>=3.7.0
fastapi>=0.111
uvicorn[standard]>=0.29
jinja2>=3.1
python-multipart>=0.0.9
aiofiles>=23.0
PyQt6>=6.6
qasync>=0.27
```

No GPU required. EasyOCR runs on CPU. The desktop app requires a display (X11 or Wayland).

---

## Discord Setup

### Bot permissions

Grant these permissions **server-wide** (or individually in every channel the bot uses).

| Permission | Integer bit | Why it is needed |
|---|---|---|
| **View Channel** | `1 << 10` | See channels and incoming messages |
| **Send Messages** | `1 << 11` | Post scan status, command responses, and error messages |
| **Read Message History** | `1 << 16` | Create message replies; required in the showcase channel for the welcome embed |
| **Embed Links** | `1 << 14` | Send `discord.Embed` objects — card confirmations, search results, stats, showcase |
| **Attach Files** | `1 << 15` | Send file attachments — deck `.txt`, export CSV/JSON, backup `.db.xz`, price-history charts |

**OAuth2 invite permission integer:** `117760`

> **Common symptom if a permission is missing**
>
> | Missing permission | What breaks |
> |---|---|
> | **Embed Links** | Scan confirmation shows buttons but no card embed; `/add`, `/search`, `/stats`, `/showcase` show no embed |
> | **Attach Files** | `/export`, `/backup create`, `/deck propose`, and showcase price-history charts fail with 403 |
> | **Read Message History** | Showcase channel welcome reply fails with 403 (error code 160002) |
> | **Send Messages** | Bot cannot post anything |

### Per-channel notes

| Channel setting | Critical permissions |
|---|---|
| `DISCORD_SCAN_CHANNEL_ID` | **Send Messages** + **Embed Links** + **Read Message History** |
| `DISCORD_SHOWCASE_CHANNEL_ID` | **Send Messages** + **Embed Links** + **Attach Files** + **Read Message History** |
| `DISCORD_DECKBUILDER_CHANNEL_ID` | **Send Messages** + **Embed Links** + **Attach Files** |
| Any channel with `/export` or `/backup` | **Send Messages** + **Attach Files** |

### Gateway intents

Two **privileged intents** must be enabled in the [Discord Developer Portal](https://discord.com/developers/applications) → *Your App → Bot → Privileged Gateway Intents*:

| Intent | Why it is needed |
|---|---|
| **Message Content Intent** | Read message content and detect image attachments in the scan channel |
| **Server Members Intent** | Resolve member display names in scan confirmations and stats |

---

## Installation

### First-time setup

```bash
git clone https://github.com/rotzbouf/mtg-collection-manager.git
cd mtg-collection-manager
bash install.sh
nano .env          # fill in DISCORD_TOKEN and channel IDs
```

The install script:

1. Checks for Python 3.10+
2. Installs `tesseract-ocr`, the German language pack, and OpenCV system libraries (`apt`, `dnf`, or `pacman`)
3. Creates a virtual environment at `./venv`
4. Installs all Python dependencies from `requirements.txt`
5. Copies `.env.example` → `.env` if no `.env` exists yet

> **Note:** On the very first run EasyOCR downloads its language models (~150 MB). This is cached automatically.

### Running as a systemd service (recommended for servers)

```bash
sudo bash service_install.sh
```

```bash
sudo systemctl status mtg-bot          # check status
sudo journalctl -u mtg-bot -f          # live logs
sudo bash service_uninstall.sh         # remove the service
```

### Updating

```bash
git pull && sudo systemctl restart mtg-bot
```

---

## Configuration

Edit `.env` after installation:

```dotenv
# Required
DISCORD_TOKEN=your_discord_bot_token_here

# Optional — restrict slash command sync to one guild (faster for development)
DISCORD_GUILD_ID=

# Channel where images are auto-scanned and write commands (add, import, resync) are restricted to
DISCORD_SCAN_CHANNEL_ID=

# Channel where /deck commands work
DISCORD_DECKBUILDER_CHANNEL_ID=

# Channel where /showcase works (leave blank = any channel)
DISCORD_SHOWCASE_CHANNEL_ID=

# Channel where /search works (leave blank = any channel)
DISCORD_SEARCH_CHANNEL_ID=

# Directory for server-side backup copies (relative or absolute path)
BACKUP_DIR=backups

# Role-based access control (role name or role ID; leave blank = everyone)
DISCORD_GUEST_ROLE=
DISCORD_COLLECTOR_ROLE=
DISCORD_ADMIN_ROLE=

# Local image cache directory (relative or absolute; default: ./images)
IMAGE_CACHE_DIR=images

# Web UI host and port (default: localhost:8000)
UI_HOST=127.0.0.1
UI_PORT=8000

# Set to 1 to receive an ephemeral debug image after each scan showing the
# isolated card and the OCR name zone (red rectangle). Disable in production.
DEBUG_SCAN_PREVIEW=0
```

### Channel restrictions

| Setting | Restricted commands | Available everywhere |
|---|---|---|
| `DISCORD_SCAN_CHANNEL_ID` | `/add`, `/import`, `/resync`, auto-scan image drops | `/search`, `/list`, `/stats`, `/export`, `/container list`, `/showcase`, `/browse` |
| `DISCORD_DECKBUILDER_CHANNEL_ID` | `/deck propose` | — |
| `DISCORD_SHOWCASE_CHANNEL_ID` | `/showcase` | — |
| `DISCORD_SEARCH_CHANNEL_ID` | `/search` | — |

### Role-based access control

```
Admin  ≥  Collector  ≥  Guest
```

| Role variable | Tier | Permissions |
|---|---|---|
| `DISCORD_GUEST_ROLE` | Guest (read-only) | `/list`, `/search`, `/stats`, `/export`, `/container list`, `/browse`, `/deck propose`, `/showcase` |
| `DISCORD_COLLECTOR_ROLE` | Collector | `/add`, `/import`, Browse → Edit/Move/Resync card, Browse → Create Container + all Guest |
| `DISCORD_ADMIN_ROLE` | Admin | Browse → Delete card/container, Browse → Rename container, `/container move`, `/resync`, `/backup create`, `/backup restore` + all Collector |

---

## Running

### Discord bot

```bash
source venv/bin/activate
python3 bot.py
```

On first start the bot:
- Initialises the SQLite database (`mtg_collection.db`)
- Syncs slash commands (instantly to the configured guild, or globally within ~1 hour)
- Loads the EasyOCR model in the background (may take a minute on the very first run)
- Downloads any missing card images to `IMAGE_CACHE_DIR` in the background (0.5 s between requests)

### Web UI

```bash
./start_ui.sh
```

Opens a browser-based collection manager at `http://localhost:8000` (or the configured `UI_HOST:UI_PORT`).
All features are available: collection browsing/editing, containers, statistics, import/export, deckbuilder, and database backup/restore.

### Desktop app

```bash
./start_desktop.sh
```

Launches a native PyQt6 application. Requires a display (X11 or Wayland).
The app connects directly to the same SQLite database used by the bot and web UI.

**Desktop-specific features:**

| Tab | What it offers |
|---|---|
| **Collection** | Paginated card list; text search + ID search field; multi-select with context menu to move/remove; edit, resync, price history, delete per card |
| **Add Card** | Name lookup with set/condition/foil/language/container picker; confirms with collection ID and current container fill count |
| **Search** | Full-text search across all fields; multi-select rows; context menu to move or remove cards from a container |
| **Containers** | Browse cards per container; multi-select context menu for move/remove; single-card actions: commander toggle, resync, price history |
| **Overcount** | Cards exceeding a configurable copy threshold; split view with card detail panel; multi-select move/remove via context menu |
| **Stats** | Totals, rarity breakdown, language split, top-5 by value |
| **Import / Export** | Moxfield CSV, full CSV, JSON import and export; backup create/restore with confirmation |
| **Settings** | Environment variables, container types, overcount exclusions, backup directory with folder picker |

---

## Command Reference

### Adding cards

#### `/add`

| Parameter | Required | Default | Description |
|---|---|---|---|
| `name` | yes | — | English or German card name |
| `container` | no | — | Container name or numeric ID |
| `set_code` | no | — | Narrow to a specific set, e.g. `MH3` |
| `language` | no | auto | Override detected language (`en` / `de`) |
| `condition` | no | `NM` | `NM` · `LP` · `MP` · `HP` · `DMG` |
| `foil` | no | false | Whether the card is a foil |
| `quantity` | no | 1 | Creates N separate collection entries |
| `notes` | no | — | Free-text personal notes |

```
/add name:Lightning Bolt set_code:M10 condition:NM quantity:4
/add name:Blitz der Unmöglichkeit language:de container:Binder 1
```

A **➕ Add Another Copy** button lets you add further copies without retyping.

#### Auto-scan (no command needed)

Drop an image into the configured scan channel.

**First scan (no container selected yet):**

```
You drop image
      │
      ▼
Container picker  ──── select existing / create new
      │
      ▼
OCR + Scryfall lookup (name, set code, collector number, language)
      │
      ▼
Confirmation embed  ──── Add  /  Add as foil  /  Skip
                          └── optional: change container for this & future scans
```

**Subsequent scans:** the bot skips the container picker and goes straight to scan + confirmation. A dropdown lets you switch containers for the current card and all future scans.

---

### Viewing the collection

#### `/list`

Browse the full collection with pagination (10 cards per page).

| Parameter | Default | Options |
|---|---|---|
| `page` | 1 | any integer |
| `sort` | `chaos` | `chaos` · `name` · `set` · `cmc` · `added` |
| `language` | all | `en` · `de` |

#### `/search`

Full-text search across every indexed field: name (EN + DE), type line, oracle text, set name, set code, collector number, rarity, mana cost, flavour text, and notes.

```
/search query:goblin haste
/search query:MH3
/search query:flying deathtouch
```

#### `/browse`

Interactive container and card browser (ephemeral).

1. A dropdown lists all containers; select one to enter it
2. Inside a container, a dropdown lists the cards (25 per page, paginated)
3. Select a card to open its action panel: **✏️ Edit** · **📦 Move** · **🔄 Resync** · **🗑️ Delete**
4. **✏️ Rename** and **🗑️ Delete Container** buttons are always visible (admin only)
5. A **➕ New Container** button is available in the container list and inside any container

#### `/stats`

- Total and unique card counts
- English / German breakdown with foil / non-foil split and EUR value
- Rarity breakdown (Common · Uncommon · Rare · Mythic) with values
- Top 5 most valuable cards with localised name and container location
- Per-container overview

An **⚠️ Overcounted Cards** button is attached to the stats response.

#### `/showcase`

Displays the 5 most valuable cards in your collection, one embed per card:

- Card name in the card's own language (English name in parentheses when different)
- Card image, current price (EUR and USD), set, collector number, rarity, condition, language, container
- **Price history chart** — attached once at least 2 daily snapshots exist

---

### Containers

#### `/container list`

Lists all containers with card count and total EUR value.

#### `/container move`

Moves **all** cards from one container to another.

```
/container move source:Binder 1 destination:Trade Box
```

---

### Import / Export

#### `/export`

| Format | Filename | Description |
|---|---|---|
| `Moxfield CSV` *(default)* | `collection_moxfield.csv` | Importable at moxfield.com |
| `CSV` | `collection.csv` | Excel-compatible; all fields |
| `JSON` | `collection.json` | Full record per card |

#### `/import`

| Format | Description |
|---|---|
| Moxfield CSV | Looked up on Scryfall by set code + collector number |
| Bot export CSV | Direct re-import of a full CSV export |
| Bot export JSON | Direct re-import of a JSON export |

---

### Backup & Restore

Backup commands are admin-only.

#### `/backup create`

1. Saves an uncompressed `.db` copy in `BACKUP_DIR` on the server
2. Sends an lzma-compressed `.db.xz` as an ephemeral Discord attachment

> **Size:** lzma compression typically reduces a 20+ MB database to ~2–4 MB, well within Discord's 8 MB file limit.

#### `/backup restore`

1. Attach a `.db`, `.db.gz`, or `.db.xz` file
2. The bot validates and shows a confirmation embed with card and container counts
3. Confirm to replace the current database

Backup and restore are also available in the web UI under **Import / Export**.

---

### Resync

#### `/resync`

Re-fetches Scryfall data and updates card text, type line, flavour text, prices, and image URL.

| Parameter | Required | Description |
|---|---|---|
| `id` | no | Collection ID; omit to resync every card |

```
/resync             ← refreshes all cards
/resync id:42       ← refreshes this entry and all copies sharing the same Scryfall ID
```

---

### Deckbuilder

Restricted to `DISCORD_DECKBUILDER_CHANNEL_ID` if configured.

#### `/deck propose`

**Commander format:** scores every legendary creature by synergy, shows top 10, builds a 100-card list.

**Timeless / Standard formats:** detects the dominant strategy and builds a 60-card list.

After a proposal, press **📦 Save to Container** to move all suggested cards into a new container.

**Deck list format:**

```
Commander
1 Atraxa, Praetors' Voice  // 📦 Binder 1

Creatures
1 Doubling Season  // 📦 Binder 1
1 Blitzschlag (Lightning Bolt)  // EN: Lightning Bolt  // 📦 Rote Box
```

---

## Chaos Sort Order

```
White → Blue → Black → Red → Green → Multicolour → Colourless/Artifact → Land
```

Within each colour group: Creature → Instant → Sorcery → Enchantment → Artifact → Planeswalker → Other, then ascending CMC, then alphabetical.

---

## Architecture

```
core/               — Shared service layer (used by bot, web UI, and desktop app)
│   database.py     — Async SQLite via aiosqlite; schema, migrations, all queries
│   scryfall.py     — Scryfall API client with rate limiting and 429 retry backoff
│   scanner.py      — Card isolation (OpenCV), OCR (EasyOCR CPU → pytesseract fallback)
│   image_cache.py  — Local card image cache (images/<scryfall_id>.<ext>)
│   sorting.py      — Chaos sort key computation
│   deckbuilder.py  — Synergy scoring and deck construction
│   exporter.py     — Moxfield CSV, full CSV, and JSON serialisation
│   importer.py     — Moxfield CSV, full CSV, and JSON parsing

cogs/               — Discord bot feature modules (discord.py Cogs)
│   collection.py   — /add, /list, /search, /resync
│   containers.py   — /container list, /container move, /browse
│   stats.py        — /stats, /showcase
│   deck.py         — /deck propose
│   import_export.py — /export, /import
│   backup.py       — /backup create, /backup restore
│   scan.py         — Auto-scan image handler
│   auth.py         — Role-based access control helpers
│   admin.py        — Admin utilities

ui/                 — Local web UI (FastAPI + Jinja2 + HTMX)
│   app.py          — FastAPI application
│   routes/         — Collection, containers, stats, deck, import/export routes
│   templates/      — Jinja2 HTML templates
│   static/         — CSS

desktop/            — Native desktop app (PyQt6 + qasync)
│   app.py          — QApplication entry point, dark stylesheet
│   main_window.py  — Sidebar navigation + stacked pages, DB initialisation
│   db.py           — Shared Database + ScryfallClient instances
│   widgets/        — Collection, Containers, Stats, ImportExport, Deck pages
│   dialogs/        — Add card, edit card, container dialogs

bot.py              — Discord bot entry point; loads cogs, schedules background tasks
```

### Database schema (SQLite)

| Table | Purpose |
|---|---|
| `collection` | One row per physical card |
| `containers` | Named storage locations |
| `collection_fts` | FTS5 virtual table, auto-synced via triggers |
| `price_history` | Daily EUR price snapshots per scryfall_id |

The database is created automatically on first run at `./mtg_collection.db`. Schema migrations run automatically on startup. WAL mode is enabled for safe concurrent access across all three interfaces.

---

## Project Structure

```
mtg_collection_manager/
├── bot.py                    ← Discord bot entry point
├── start_ui.sh               ← Launch web UI
├── start_desktop.sh          ← Launch desktop app
├── install.sh
├── service_install.sh
├── service_uninstall.sh
├── requirements.txt
├── .env.example
│
├── core/                     ← Shared service layer
│   ├── database.py
│   ├── scryfall.py
│   ├── scanner.py
│   ├── image_cache.py
│   ├── sorting.py
│   ├── deckbuilder.py
│   ├── exporter.py
│   └── importer.py
│
├── cogs/                     ← Discord bot feature modules
│   ├── collection.py
│   ├── containers.py
│   ├── stats.py
│   ├── deck.py
│   ├── import_export.py
│   ├── backup.py
│   ├── scan.py
│   ├── auth.py
│   └── admin.py
│
├── ui/                       ← Web UI (FastAPI + Jinja2 + HTMX)
│   ├── app.py
│   ├── routes/
│   ├── templates/
│   └── static/
│
├── desktop/                  ← Desktop app (PyQt6)
│   ├── app.py
│   ├── main_window.py
│   ├── db.py
│   ├── widgets/
│   └── dialogs/
│
├── images/                   ← Local card image cache (auto-populated at startup)
├── backups/                  ← Server-side backup copies
└── mtg_collection.db         ← SQLite database (created on first run)
```
