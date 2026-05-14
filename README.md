# MTG Collection Manager

A Discord bot for tracking your physical Magic: The Gathering collection.
Add cards by name or photo, organise them into containers (binders, boxes, decks),
search and export your collection, and generate deck proposals — all from Discord slash commands.

---

## Features

| Category | What it does |
|---|---|
| **Add by name** | Resolves English or German card names via Scryfall; auto-detects language |
| **Add by photo** | OCR reads card name, set code, collector number, and language from the photo; matched via Scryfall |
| **Auto-scan channel** | Drop any image in the configured channel — the bot processes it instantly |
| **Containers** | Organise cards into named binders, boxes, decks, trade piles, etc. |
| **Full-text search** | SQLite FTS5 across name, type, oracle text, set, flavour text, notes |
| **Chaos sort** | MTG-native sort: W→U→B→R→G→Multi→Colourless→Land, then type, then CMC |
| **Statistics** | Totals by language, foil/non-foil, rarity breakdown, top-5 by value |
| **Export** | Moxfield CSV (default), Excel CSV, or JSON |
| **Deckbuilder** | Auto-generates Commander (100-card) or 60-card (Timeless/Standard) proposals |
| **Showcase** | `/showcase` displays the 5 most valuable cards with image, details, and a price-history chart |
| **Price history** | Prices are snapshotted daily; history chart auto-appears once 2+ data points exist |
| **Overcount** | `/overcount` lists every non-basic-land card that appears more than 4 times, with per-container breakdown |
| **Backup & restore** | `/backup create` downloads a `.db` snapshot; `/backup restore` replaces the database from a file |

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

# Channel where images are auto-scanned and write/scan commands are restricted to
DISCORD_SCAN_CHANNEL_ID=

# Channel where /deck commands work
DISCORD_DECKBUILDER_CHANNEL_ID=

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
| `DISCORD_SCAN_CHANNEL_ID` | `/add`, `/scan`, `/update`, `/remove`, `/container create/rename/delete`, auto-scan image drops | `/search`, `/list`, `/card`, `/stats`, `/export`, `/container list`, `/help`, `/showcase` |
| `DISCORD_DECKBUILDER_CHANNEL_ID` | `/deck propose` | — |
| `DISCORD_SHOWCASE_CHANNEL_ID` | `/showcase` | — |

Leave all blank to allow all commands anywhere.

### Role-based access control

The bot enforces a three-tier hierarchy. Higher tiers inherit all lower-tier permissions.

```
Admin  ≥  Collector  ≥  Guest
```

| Role variable | Tier | Commands |
|---|---|---|
| `DISCORD_GUEST_ROLE` | Guest (read-only) | `/list`, `/card`, `/search`, `/stats`, `/export`, `/container list`, `/deck propose` |
| `DISCORD_COLLECTOR_ROLE` | Collector | `/add`, `/scan`, `/update`, `/container create` + all Guest commands |
| `DISCORD_ADMIN_ROLE` | Admin | `/remove`, `/container delete`, `/container rename` + all Collector commands |

Each variable accepts a **role name** (e.g. `Guest`) or a **role ID** (e.g. `123456789`).

| Scenario | Effect |
|---|---|
| `DISCORD_GUEST_ROLE` not set | Read-only commands open to everyone |
| `DISCORD_COLLECTOR_ROLE` not set | Collector commands open to everyone |
| `DISCORD_ADMIN_ROLE` not set | Admin commands open to everyone |
| All three not set | Fully open — no role restrictions |

`/help` is always accessible regardless of roles.

---

## Running manually

```bash
source venv/bin/activate
python bot.py
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
| `container` | no | — | Container name or ID; created automatically if new |
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

#### `/scan`

Add a card by attaching a photo. OCR reads the card name and footer (set code, collector number, language) and matches via Scryfall.

| Parameter | Required | Default | Description |
|---|---|---|---|
| `image` | yes | — | Photo of the card |
| `condition` | no | `NM` | Card condition |
| `foil` | no | false | Whether the card is a foil |
| `quantity` | no | 1 | Number of copies |
| `language` | no | auto | Override detected language |

#### Auto-scan (no command needed)

Drop an image directly into the configured scan channel.
The bot replies with a container picker, identifies the card, then asks you to confirm before saving.

**Scan flow:**

```
You drop image
      │
      ▼
Container picker  ──── select existing / create new
      │
      ▼
OCR name + footer (set code, collector #, language) — run in parallel
      │
      ▼
Collector match (set code + number → Scryfall)
      │  if no match ▼
      OCR name → Scryfall (with set code hint)
      │
      ▼
Confirmation embed  ──── Add  /  Add as foil  /  Wrong card?  /  Skip
```

If the bot misidentifies the card, tap **Wrong card?** to type the correct name and set code.

---

### Viewing the collection

#### `/list`

Browse the full collection with pagination (10 cards per page).

| Parameter | Default | Options |
|---|---|---|
| `page` | 1 | any integer |
| `sort` | `chaos` | `chaos` · `name` · `set` · `cmc` · `added` |
| `language` | all | `en` · `de` |

#### `/card`

Show full details for a single entry by its collection ID.

```
/card id:42
```

#### `/search`

Full-text search across every indexed field: name (EN + DE), type line, oracle text,
set name, set code, collector number, rarity, mana cost, flavour text, and notes.

```
/search query:goblin haste
/search query:MH3
/search query:flying deathtouch
```

Returns up to 15 results sorted by chaos order.

#### `/stats`

Collection-wide statistics:

- Total and unique card counts
- English / German breakdown with foil / non-foil split and EUR value
- Rarity breakdown (Common · Uncommon · Rare · Mythic) with values
- Top 5 most valuable cards with container location
- Per-container overview with bulk detection (containers where the most expensive card ≤ €0.05 are flagged as bulk)

#### `/overcount`

Lists every non-basic-land card that appears more than 4 times in your collection, sorted by count (highest first).
For each card the total copy count and a per-container breakdown are shown:

```
Lightning Bolt — 7×
  📦 Binder 1: 4  ·  📦 Trade Box: 3
```

Useful for identifying surplus copies before trading or selling.

#### `/showcase`

Displays the 5 most valuable cards in your collection, one embed per card:

- Card image (thumbnail)
- Current price (EUR and USD where available)
- Set, collector number, rarity, condition, language, container location
- **Price history chart** — a line chart is automatically attached once at least 2 daily snapshots exist

Prices are recorded automatically once per day in the background. The chart appears without any manual action after the bot has been running for two days.

If `DISCORD_SHOWCASE_CHANNEL_ID` is set, this command is restricted to that channel.

---

### Editing

#### `/update`

Change a single field on an existing entry.

| Parameter | Description |
|---|---|
| `id` | Collection entry ID |
| `field` | `condition` · `foil` · `language` · `notes` · `price_eur` · `price_usd` |
| `value` | New value |

```
/update id:42 field:condition value:LP
/update id:42 field:foil value:1
/update id:42 field:notes value:"signed by artist"
```

#### `/remove`

Remove a card entry by ID. Asks for confirmation before deleting.

```
/remove id:42
```

---

### Containers

Containers represent physical storage locations — binders, deck boxes, trade piles, etc.
Each card can belong to one container. Deleting a container does **not** delete its cards.

#### `/container create`

```
/container create name:Binder 1 type:binder description:Red/Blue staples
```

Available types: `binder` · `box` · `deck` · `trade` · `other`

#### `/container list`

Lists all containers with card count and total EUR value.

#### `/container rename`

```
/container rename id:3 name:Legacy Binder
```

#### `/container delete`

Removes the container; cards are unlinked but kept in the collection.

```
/container delete id:3
```

---

### Export

#### `/export`

Downloads your entire collection as a file attachment.

| Format | Filename | Description |
|---|---|---|
| `Moxfield CSV` *(default)* | `collection_moxfield.csv` | Importable at moxfield.com; columns: Count, Name, Edition, Condition, Language, Foil, Collector Number |
| `CSV` | `collection.csv` | Excel-compatible; all fields including Scryfall metadata |
| `JSON` | `collection.json` | Full record per card including all Scryfall metadata |

**Moxfield import:** go to your Moxfield collection → *Import* → upload `collection_moxfield.csv`.

---

### Backup & Restore

Backup and restore commands are admin-only.

#### `/backup create`

Creates a consistent snapshot of the current database and sends it as an ephemeral file attachment (e.g. `mtg_collection_20260514_143022.db`).
The snapshot is taken online — no downtime or connection interruption required.

#### `/backup restore`

Restores the database from a previously created backup file.

1. Attach the `.db` file produced by `/backup create`
2. The bot validates the file and shows a confirmation embed with the card and container counts found in the backup
3. Confirm to replace the current database — all changes made after the backup was created will be lost
4. The bot reinitialises the database and applies any pending migrations automatically

> **Tip:** Run `/backup create` before a bulk import or any other operation you may want to undo.

---

### Deckbuilder

Deckbuilder commands are restricted to `DISCORD_DECKBUILDER_CHANNEL_ID` (if configured).

#### `/deck propose`

Generates a deck proposal from your collection using synergy scoring.

**Commander format**

1. The bot scores every legendary creature in your collection by synergy with the rest of your cards
2. A dropdown shows the top 10 candidates with colour identity and synergy score
3. Pick a commander → the bot builds a 100-card list (up to 63 non-lands + basic lands)
4. The result embed lists the top key cards with their **container location** (📦 binder / box)
5. A `.txt` deck list is attached — every line includes the container where the card is stored
6. Press **✅ Accept** to dismiss the proposal when you're done

**Timeless / Standard formats**

Automatically detects the dominant strategy in your legal cards (tokens, counters, graveyard, control, etc.) and builds a 60-card list (36 non-lands + 24 basic lands).
The attached `.txt` file includes the container location per card, and a **✅ Accept** button lets you dismiss the result.

**Deck list format**

```
Commander
1 Atraxa, Praetors' Voice  // 📦 Binder 1

Creatures
1 Doubling Season  // 📦 Binder 1
1 Vorinclex, Monstrous Raider  // 📦 Commander Box
...

Basic Lands
20 Forest
16 Plains
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
