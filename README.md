# MTG Collection Manager

A Discord bot for tracking your physical Magic: The Gathering collection.
Add cards by name or photo, organise them into containers (binders, boxes, decks),
search and export your collection, and generate deck proposals — all from Discord.

---

## Features

| Category | What it does |
|---|---|
| **Add by name** | Resolves English or German card names via Scryfall; auto-detects language |
| **Add by photo** | Drop an image in the scan channel; OCR reads card name, set code, collector number, and language |
| **Container memory** | After picking a container once, subsequent scans go straight to confirmation — no repeated selection |
| **Localised card text & names** | Type line, oracle text, flavour text, and display names are stored in the card's own language |
| **Containers** | Organise cards into named binders, boxes, decks, trade piles, etc. Create, rename, and delete via Browse |
| **Full-text search** | SQLite FTS5 across name, type, oracle text, set, flavour text, notes — paginated with card picker |
| **Interactive browsing** | `/browse` opens a container-and-card browser; click any card to edit, move, resync, or delete it; manage the container (rename/delete) from the same view |
| **Chaos sort** | MTG-native sort: W→U→B→R→G→Multi→Colourless→Land, then type, then CMC |
| **Statistics** | Totals by language, foil/non-foil, rarity breakdown, top-5 by value; Overcounted Cards button |
| **Export** | Moxfield CSV (default), Excel CSV, or JSON |
| **Import** | Moxfield CSV, bot-export CSV, or bot-export JSON |
| **Deckbuilder** | Auto-generates Commander (100-card) or 60-card (Timeless/Standard) proposals; saves the deck to a container |
| **Deck list** | Every proposed card shows its storage location; a location manifest at the end records original container IDs |
| **Showcase** | `/showcase` displays the 5 most valuable cards with image, details, and a price-history chart |
| **Price history** | Prices are snapshotted daily; history chart auto-appears once 2+ data points exist |
| **Null-price refresh** | Cards added without a EUR price are automatically re-checked against Scryfall daily |
| **Overcount** | Cards with more than 4 copies — shown via the Overcounted Cards button in `/stats`, with per-container breakdown and a move UI |
| **Backup & restore** | `/backup create` saves a copy on the server and sends a compressed `.db.gz`; `/backup restore` accepts both formats |
| **Resync** | `/resync` re-fetches fresh Scryfall data (text, prices, image) for one or all cards |

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
```

No GPU required. EasyOCR runs on CPU; the scan rate of a Discord bot makes CPU inference fast enough.

---

## Discord Setup

### Bot permissions

Grant these permissions **server-wide** (or individually in every channel the bot uses).

| Permission | Integer bit | Why it is needed |
|---|---|---|
| **View Channel** | `1 << 10` | See channels and incoming messages |
| **Send Messages** | `1 << 11` | Post scan status, command responses, and error messages |
| **Read Message History** | `1 << 16` | Create message replies (`message.reply`); required in the showcase channel for the welcome embed |
| **Embed Links** | `1 << 14` | Send `discord.Embed` objects — card confirmations, search results, stats, showcase, `/add` output |
| **Attach Files** | `1 << 15` | Send file attachments — deck `.txt`, export CSV/JSON, backup `.db.gz`, price-history chart images |

**OAuth2 invite permission integer:** `117760`  
_(View Channel + Send Messages + Read Message History + Embed Links + Attach Files)_

> **Common symptom if a permission is missing**
>
> | Missing permission | What breaks |
> |---|---|
> | **Embed Links** | Scan confirmation shows buttons but no card embed; `/add`, `/search`, `/stats`, `/showcase` show no embed |
> | **Attach Files** | `/export`, `/backup create`, `/deck propose`, and showcase price-history charts fail with 403 |
> | **Read Message History** | Showcase channel welcome reply fails with 403 (error code 160002) |
> | **Send Messages** | Bot cannot post anything |

### Per-channel notes

If channel-specific permission overrides exist, make sure the bot's role is not denied any of the above. Channels configured in `.env` have the following specific requirements:

| Channel setting | Critical permissions |
|---|---|
| `DISCORD_SCAN_CHANNEL_ID` | **Send Messages** + **Embed Links** (card confirmation embeds); **Read Message History** only if you want native message replies |
| `DISCORD_SHOWCASE_CHANNEL_ID` | **Send Messages** + **Embed Links** + **Attach Files** (chart images) + **Read Message History** (welcome reply) |
| `DISCORD_DECKBUILDER_CHANNEL_ID` | **Send Messages** + **Embed Links** + **Attach Files** (deck `.txt`) |
| Any channel with `/export` or `/backup` | **Send Messages** + **Attach Files** |

### Gateway intents

Two **privileged intents** must be enabled in the [Discord Developer Portal](https://discord.com/developers/applications) → *Your App → Bot → Privileged Gateway Intents*:

| Intent | Why it is needed |
|---|---|
| **Message Content Intent** | Read message content and detect image attachments in the scan channel |
| **Server Members Intent** | Resolve member display names in scan confirmations and stats |

Without **Message Content Intent** the auto-scan feature will not trigger on image drops.

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

The service starts automatically on boot and restarts on failure. Logs go to the system journal.

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

Leave all blank to allow all commands anywhere.

### Role-based access control

The bot enforces a three-tier hierarchy. Higher tiers inherit all lower-tier permissions.

```
Admin  ≥  Collector  ≥  Guest
```

| Role variable | Tier | Permissions |
|---|---|---|
| `DISCORD_GUEST_ROLE` | Guest (read-only) | `/list`, `/search`, `/stats`, `/export`, `/container list`, `/browse`, `/deck propose`, `/showcase` |
| `DISCORD_COLLECTOR_ROLE` | Collector | `/add`, `/import`, Browse → Edit/Move/Resync card, Browse → Create Container + all Guest |
| `DISCORD_ADMIN_ROLE` | Admin | Browse → Delete card/container, Browse → Rename container, `/container move`, `/resync`, `/backup create`, `/backup restore` + all Collector |

Each variable accepts a **role name** (e.g. `Guest`) or a **role ID** (e.g. `123456789`).

| Scenario | Effect |
|---|---|
| `DISCORD_GUEST_ROLE` not set | Read-only commands open to everyone |
| `DISCORD_COLLECTOR_ROLE` not set | Collector commands open to everyone |
| `DISCORD_ADMIN_ROLE` not set | Admin commands open to everyone |
| All three not set | Fully open — no role restrictions |

---

## Running manually

```bash
source venv/bin/activate
python3 bot.py
```

Logs go to stdout. Use the systemd service for production deployments.

On first start the bot:

- Initialises the SQLite database (`mtg_collection.db`)
- Syncs slash commands (instantly to the configured guild, or globally within ~1 hour)
- Loads the EasyOCR model in the background (may take a minute on the very first run)

---

## Command Reference

### Adding cards

#### `/add`

Add a card by name. Scryfall is queried automatically.

| Parameter | Required | Default | Description |
|---|---|---|---|
| `name` | yes | — | English or German card name |
| `container` | no | — | Container name (created automatically if new) or numeric container ID |
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

After adding, a **➕ Add Another Copy** button lets you add further copies of the same card without retyping the command.

#### Auto-scan (no command needed)

Drop an image directly into the configured scan channel.

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

**Subsequent scans (container already known):**

The bot skips the container picker and goes straight to scan + confirmation, showing which container will be used. A dropdown in the confirmation embed lets you switch containers for the current card and all future scans.

---

### Viewing the collection

#### `/list`

Browse the full collection with pagination (10 cards per page). Navigation buttons (◀ / ▶) appear when there is more than one page.
A **card picker dropdown** is shown on every page — select any card to open its full action panel without leaving the list.

| Parameter | Default | Options |
|---|---|---|
| `page` | 1 | any integer |
| `sort` | `chaos` | `chaos` · `name` · `set` · `cmc` · `added` |
| `language` | all | `en` · `de` |

#### `/search`

Full-text search across every indexed field: name (EN + DE), type line, oracle text,
set name, set code, collector number, rarity, mana cost, flavour text, and notes.

```
/search query:goblin haste
/search query:MH3
/search query:flying deathtouch
```

Results are paginated (10 per page) with ◀ / ▶ navigation. A **card picker dropdown** lets you open any result's action panel directly.

#### `/browse`

Interactive container and card browser (ephemeral — only visible to you).

1. A dropdown lists all containers; select one to enter it
2. Inside a container, a dropdown lists the cards (25 per page, paginated)
3. Select a card to open its action panel: **✏️ Edit** · **📦 Move** · **🔄 Resync** · **🗑️ Delete**
4. Use **◀ Containers** to return to the container list
5. **✏️ Rename** and **🗑️ Delete Container** buttons are always visible at the bottom of any container view (admin only)
6. A **➕ New Container** button is available both in the container list and inside any container

The action panel (for individual cards) provides: **✏️ Edit** (condition, language, foil, notes) · **📦 Move** (to another container) · **🔄 Resync** (re-fetch from Scryfall) · **🗑️ Delete**.

#### `/stats`

Collection-wide statistics:

- Total and unique card counts
- English / German breakdown with foil / non-foil split and EUR value
- Rarity breakdown (Common · Uncommon · Rare · Mythic) with values
- Top 5 most valuable cards with container location
- Per-container overview with bulk detection (containers where the most expensive card ≤ €0.05 are flagged as bulk)

An **⚠️ Overcounted Cards** button is attached to the stats response. Click it to see every card that appears more than 4 times, with a per-container breakdown, price summary, and a UI for selecting and moving excess copies to another container.

#### `/showcase`

Displays the 5 most valuable cards in your collection, one embed per card:

- Card name in the card's own language (English name shown in parentheses when different)
- Card image (thumbnail)
- Current price (EUR and USD where available)
- Set, collector number, rarity, condition, language, container location
- **Price history chart** — a line chart is automatically attached once at least 2 daily snapshots exist

Prices are recorded automatically once per day in the background. The chart appears without any manual action after the bot has been running for two days.

A second background task runs daily to back-fill EUR prices for cards that had no price when they were first added. No manual action needed.

If `DISCORD_SHOWCASE_CHANNEL_ID` is set, posting in that channel triggers a welcome menu with quick-access buttons (Showcase, Browse, Stats, Commands).

---

### Containers

Containers represent physical storage locations — binders, deck boxes, trade piles, etc.
Each card can belong to one container. Deleting a container does **not** delete its cards.

All container management (create, rename, delete) is accessible through `/browse` or `/container list`.

#### `/container list`

Lists all containers with card count and total EUR value.
Buttons: **📦 Browse** (opens the interactive container browser) · **➕ New Container** (creates a new container).

#### `/container move`

Moves **all** cards from one container to another in a single operation.

```
/container move source:Binder 1 destination:Trade Box
```

Both `source` and `destination` support autocomplete.

**Creating, renaming, and deleting containers** is done via Browse:
- **Create:** click **➕ New Container** in the container list or inside any container
- **Rename:** enter a container, then click **✏️ Rename** (admin)
- **Delete:** enter a container, then click **🗑️ Delete Container** — cards are kept, only the container link is removed (admin)

---

### Import / Export

#### `/export`

Downloads your entire collection as a file attachment.

| Format | Filename | Description |
|---|---|---|
| `Moxfield CSV` *(default)* | `collection_moxfield.csv` | Importable at moxfield.com; columns: Count, Name, Edition, Condition, Language, Foil, Collector Number |
| `CSV` | `collection.csv` | Excel-compatible; all fields including Scryfall metadata |
| `JSON` | `collection.json` | Full record per card including all Scryfall metadata |

**Moxfield import:** go to your Moxfield collection → *Import* → upload `collection_moxfield.csv`.

#### `/import`

Imports cards from an attached file into the collection.

| Format | Description |
|---|---|
| Moxfield CSV | Each card is looked up on Scryfall by set code + collector number (falls back to name search) |
| Bot export CSV | Direct import of a previously exported full CSV |
| Bot export JSON | Direct import of a previously exported JSON |

Optionally assign all imported cards to a specific container.
A preview of the import (entry count, format detected) is shown before confirmation.

> **Tip:** Run `/backup create` before a large import so you can roll back if needed.

---

### Backup & Restore

Backup and restore commands are admin-only.

#### `/backup create`

Creates a consistent snapshot of the current database:

1. Saves an uncompressed `.db` copy in `BACKUP_DIR` on the server (default: `./backups/`)
2. Sends a gzip-compressed `.db.gz` as an ephemeral Discord attachment for download

The snapshot is taken online — no downtime or connection interruption required.

#### `/backup restore`

Restores the database from a previously created backup file.

1. Attach a `.db` or `.db.gz` file (both formats accepted)
2. The bot validates the file and shows a confirmation embed with the card and container counts found in the backup
3. Confirm to replace the current database — all changes made after the backup was created will be lost
4. The bot reinitialises the database and applies any pending migrations automatically

> **Tip:** Run `/backup create` before a bulk import or any other operation you may want to undo.

---

### Resync

#### `/resync`

Re-fetches fresh data from Scryfall and updates all Scryfall-sourced fields: card text, type line, flavour text, prices, and image URL. Sort keys are recomputed. Collection metadata (condition, foil, language, notes, container) is preserved.

| Parameter | Required | Description |
|---|---|---|
| `id` | no | Collection ID of the card to resync; omit to resync every card in the collection |

```
/resync             ← refreshes all cards (shows progress every 25 cards)
/resync id:42       ← refreshes this entry and all copies sharing the same Scryfall ID
```

Individual cards can also be resynced from their action panel in `/browse` or `/list`.

---

### Deckbuilder

Deckbuilder commands are restricted to `DISCORD_DECKBUILDER_CHANNEL_ID` (if configured).

#### `/deck propose`

Generates a deck proposal from your collection using synergy scoring.

**Commander format**

1. The bot scores every legendary creature in your collection by synergy with the rest of your cards
2. A dropdown shows the top 10 candidates with colour identity and synergy score
3. Pick a commander → the bot builds a 100-card list (up to 63 non-lands + basic lands from your collection)
4. The result embed lists the top key cards with their **container location** (📦 binder / box)
5. A `.txt` deck list is attached — every line includes the container where the card is stored

**Timeless / Standard formats**

Automatically detects the dominant strategy in your legal cards (tokens, counters, graveyard, control, etc.) and builds a 60-card list (36 non-lands + 24 basic lands). Basic lands are taken from your collection first; any shortfall is noted as plain text entries.

**Saving a deck**

Press **📦 Save to Container** after a deck proposal to move all suggested cards (including collection basics) into a new container named after the deck. The original container of each card is recorded in the `.txt` file's location manifest so you can trace every card back after the move.

**Deck list format**

Non-English cards show their printed name with the English name in parentheses:

```
Commander
1 Atraxa, Praetors' Voice  // 📦 Binder 1

Creatures
1 Doubling Season  // 📦 Binder 1
1 Blitzschlag (Lightning Bolt)  // EN: Lightning Bolt  // 📦 Rote Box
...

Basic Lands
1 Forest  // 📦 Binder 1
4 Forest
```

A **location manifest** at the end of the `.txt` lists each card's collection ID, container ID, and container name at proposal time:

```
// --- Location Manifest ---
Card ID  Cont. ID  Container   Card (localized / EN)
1042     3         Rote Box    Blitzschlag / Lightning Bolt
1095     7         Blue Box    Counterspell
```

---

## Chaos Sort Order

The default sort mimics how experienced players physically sort their collection:

```
White → Blue → Black → Red → Green → Multicolour → Colourless/Artifact → Land
```

Within each colour group:

```
Creature → Instant → Sorcery → Enchantment → Artifact → Planeswalker → Other
```

Within each type group: ascending CMC, then alphabetical by name.

---

## Architecture

```
bot.py          — Discord bot, all slash commands, UI views, auto-scan handler
database.py     — Async SQLite via aiosqlite; schema, migrations, all queries
scanner.py      — Card isolation (OpenCV), OCR (EasyOCR CPU → pytesseract fallback)
scryfall.py     — Scryfall API client: card lookup, EN/DE name resolution
sorting.py      — Chaos sort key computation
deckbuilder.py  — Synergy scoring, deck construction, deck list formatting
exporter.py     — Moxfield CSV, full CSV, and JSON serialisation
importer.py     — Moxfield CSV, full CSV, and JSON parsing for /import
```

### Database schema (SQLite)

| Table | Purpose |
|---|---|
| `collection` | One row per physical card |
| `containers` | Named storage locations |
| `collection_fts` | FTS5 virtual table, auto-synced via triggers |
| `price_history` | Daily EUR price snapshots per scryfall_id (for `/showcase` charts) |

The database is created automatically on first run at `./mtg_collection.db`.
Schema migrations run automatically on startup.

---

## Project Structure

```
mtg_collection_manager/
├── bot.py
├── database.py
├── deckbuilder.py
├── exporter.py
├── importer.py
├── scanner.py
├── scryfall.py
├── sorting.py
├── requirements.txt
├── install.sh
├── service_install.sh
├── service_uninstall.sh
├── .env.example
└── mtg_collection.db      ← created on first run
```
