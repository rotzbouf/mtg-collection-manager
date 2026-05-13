# MTG Collection Manager

A Discord bot for tracking your physical Magic: The Gathering collection.
Add cards by name or photo, organise them into containers (binders, boxes, decks),
search and export your collection, and generate deck proposals — all from Discord slash commands.

---

## Features

| Category | What it does |
|---|---|
| **Add by name** | Resolves English or German card names via Scryfall; auto-detects language |
| **Add by photo** | Runs visual hash matching and OCR simultaneously; hash = card identity, OCR = language |
| **Auto-scan channel** | Drop any image in the configured channel — the bot processes it instantly |
| **Containers** | Organise cards into named binders, boxes, decks, trade piles, etc. |
| **Full-text search** | SQLite FTS5 across name, type, oracle text, set, flavour text, notes |
| **Chaos sort** | MTG-native sort: W→U→B→R→G→Multi→Colourless→Land, then type, then CMC |
| **Statistics** | Totals by language, foil/non-foil, rarity breakdown, top-5 by value |
| **Export** | Moxfield CSV (default), Excel CSV, or JSON |
| **Deckbuilder** | Auto-generates Commander (100-card) or 60-card (Timeless/Standard) proposals |
| **Hash index** | Perceptual hash index built concurrently with downloads; GPU or CPU |
| **Daily set check** | Notifies you when Scryfall has new sets not yet in the hash index |

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
imagehash>=4.3.1
numpy>=1.24.0
opencv-python-headless>=4.8.0
```

### GPU support (optional)

When an NVIDIA GPU is present the install script automatically installs PyTorch with CUDA support.

| Component | GPU effect |
|---|---|
| **EasyOCR** | OCR inference 3–10× faster |
| **pHash index build** | Card images hashed in GPU batches of 64 (DCT on GPU), pipelined with downloads |
| **pHash query** | Query hashes computed on GPU |

By default the bot pins itself to **GPU 0**. Override via `CUDA_VISIBLE_DEVICES` in `.env` (see Configuration).

Without a GPU everything falls back to CPU automatically — no configuration needed.

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
3. Detects NVIDIA GPU via `nvidia-smi` and installs PyTorch with the best available CUDA wheel; falls back to CPU PyTorch if no GPU is found
4. Creates a virtual environment at `./venv`
5. Installs all Python dependencies from `requirements.txt`
6. Copies `.env.example` → `.env` if no `.env` exists yet

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

# Channel where images are auto-scanned and all collection commands work
DISCORD_SCAN_CHANNEL_ID=

# Channel where /deck commands work
DISCORD_DECKBUILDER_CHANNEL_ID=

# GPU — which CUDA device(s) PyTorch may use (default: 0 only)
# Use "0,1" to allow both GPUs, or "1" to use only GPU 1.
CUDA_VISIBLE_DEVICES=0

# Role-based access control (role name or role ID; leave blank = everyone)
DISCORD_GUEST_ROLE=
DISCORD_COLLECTOR_ROLE=
DISCORD_ADMIN_ROLE=

# Set to 1 to receive an ephemeral debug image after each scan showing the
# isolated card and the OCR name zone (red rectangle). Disable in production.
DEBUG_SCAN_PREVIEW=0
```

If `DISCORD_SCAN_CHANNEL_ID` is set, all collection commands and image drops are restricted to that channel.
If `DISCORD_DECKBUILDER_CHANNEL_ID` is set, `/deck` commands only work there.
Leave both blank to allow commands anywhere.

### Role-based access control

The bot enforces a three-tier hierarchy. Higher tiers inherit all lower-tier permissions.

```
Admin  ≥  Collector  ≥  Guest
```

| Role variable | Tier | Commands |
|---|---|---|
| `DISCORD_GUEST_ROLE` | Guest (read-only) | `/list`, `/card`, `/search`, `/stats`, `/export`, `/index status`, `/index check`, `/container list`, `/deck propose` |
| `DISCORD_COLLECTOR_ROLE` | Collector | `/add`, `/scan`, `/update`, `/container create` + all Guest commands |
| `DISCORD_ADMIN_ROLE` | Admin | `/remove`, `/container delete`, `/container rename`, `/index update`, `/index rebuild` + all Collector commands |

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
- Pins the pHash engine and EasyOCR to GPU 0 (or CPU if no GPU is available)
- Loads the EasyOCR model in the background (may take a minute on the very first run)
- Starts a daily background check for new Scryfall sets

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

Add a card by attaching a photo. Uses visual hash matching and OCR simultaneously.

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
Visual hash match  ──────────────────────┐
OCR → Scryfall lookup (runs in parallel) │
      │                                  │
      │  hash  →  card identity          │
      │  OCR   →  language (EN/DE)       │
      │                                  │
      └──────────── combine ─────────────┘
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

### Visual hash index

The hash index stores perceptual hashes (pHash) of card images downloaded from Scryfall.
When you drop a photo, the bot compares it against the entire index in milliseconds.

Building the index uses a producer-consumer pipeline: image downloads and GPU hashing run
concurrently, so the GPU starts working as soon as the first batch of images arrives.

#### `/index status`

Shows indexed card count, in-memory cache size, and when the index was last built.

#### `/index check`

Lists all Scryfall sets not yet present in the hash index.

#### `/index update`

Downloads card images for any unindexed sets and adds them to the index.
A live progress bar shows download progress (MB) and hashing status.

#### `/index rebuild`

Wipes the entire index and re-downloads all card images from scratch.
Use this after changing GPU availability or if the index appears corrupted.

> **Note:** The first `/index update` or rebuild can take a long time depending on how many sets are missing. The bot remains fully usable during the process.

---

### Deckbuilder

Deckbuilder commands are restricted to `DISCORD_DECKBUILDER_CHANNEL_ID` (if configured).

#### `/deck propose`

Generates a deck proposal from your collection using synergy scoring.

**Commander format**

1. The bot scores every legendary creature in your collection by synergy with the rest of your cards
2. A dropdown shows the top 10 candidates with colour identity and synergy score
3. Pick a commander → the bot builds a 100-card list (up to 63 non-lands + basic lands) and attaches it as a `.txt` file

**Timeless / Standard formats**

Automatically detects the dominant strategy in your legal cards (tokens, counters, graveyard, control, etc.) and builds a 60-card list (36 non-lands + 24 basic lands), attached as `.txt`.

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
scanner.py      — Card isolation (OpenCV), OCR (EasyOCR on GPU → pytesseract fallback)
card_index.py   — Perceptual hash index: GPU-batched build, vectorised matching
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
| `card_hashes` | Perceptual hash index (scryfall_id → pHash) |
| `index_meta` | Key/value store for index metadata |

The database is created automatically on first run at `./mtg_collection.db`.
Schema migrations run automatically on startup.

---

## Project Structure

```
mtg_collection_manager/
├── bot.py
├── card_index.py
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
