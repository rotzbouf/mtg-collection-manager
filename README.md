# MTG Collection Manager

A native desktop application for tracking a physical Magic: The Gathering collection.
Built with PyQt6 — fast, offline-first, no cloud account required.

**Current version: 0.5.2**

---

## Highlights

| | |
|---|---|
| **Card lookup** | Add cards by name, set code + collector number, or webcam scan (Tesseract OCR) |
| **Dual price sources** | Scryfall EUR/USD prices with a three-level fallback chain + Cardmarket Trend/Avg30 from the official CM price guide |
| **Intelligent Deck Builder** | Builds Commander and 60-card decks from your collection — archetype detection, synergy scoring, competitive meta integration, role-slot guarantees |
| **Deck Rating** | Composite S–F grade: synergy · mana-curve fit · role coverage · archetype coherence |
| **Improve Deck** | Meta-backed swap proposals for existing decks; accepts a swap and physically moves containers |
| **Buylist tools** | Match your collection against store buylists; Brave Search API discovers stores by keyword; ranks by total payout |
| **Set Completion** | Owned vs. total cards per set with a completion %; rarity-coloured card list |
| **Statistics** | Collection value over time, top-10 cards by value, rarity/language breakdown |
| **Full-text search** | SQLite FTS5 across name, type line, oracle text, set, flavour text, notes |
| **Multi-language UI** | English and German — switch in Settings, restarts app |

---

## Screenshots

### Add Card
Type set code + collector number, press Enter to search, Ctrl+Enter to add. Focus returns to the collector number field — no mouse needed for bulk entry.

![Add / Scan](docs/screenshots/01_add_scan.png)

### Collection
Browse all cards across Collection, Containers, and Overcount. Click any row for full card details — Scryfall and Cardmarket prices side by side, with a Flip button for double-faced cards.

![Collection](docs/screenshots/02_collection.png)

### Deck Builder & Analysis
Build a Commander or 60-card deck from your collection, or analyse an existing one for mana curve, synergies, and quality grade.

![Decks](docs/screenshots/03_decks.png)

### Advanced Search
Filter by name, type line, oracle text, set, colours, rarity, mana value, condition, language, foil, and container — all at once.

![Search](docs/screenshots/04_search.png)

### Statistics
Total value, rarity and language breakdown, top-10 cards with cover art, and a collection value chart over time.

![Statistics](docs/screenshots/05_statistics.png)

---

## Quick Start

### Pre-built binaries (recommended)

Download the latest release from the [Releases page](https://github.com/rotzbouf/mtg-collection-manager/releases):

| Platform | File |
|---|---|
| Linux (x86-64) | `mtg-collection-manager-linux` |
| Windows | `mtg-collection-manager-windows.exe` |
| macOS (Apple Silicon) | `mtg-collection-manager-macos` |

> **macOS:** The binary is not code-signed. To allow it: right-click → **Open**, or run once in Terminal:
> ```bash
> xattr -d com.apple.quarantine mtg-collection-manager-macos
> chmod +x mtg-collection-manager-macos
> ./mtg-collection-manager-macos
> ```

### From source

```bash
git clone https://github.com/rotzbouf/mtg-collection-manager.git
cd mtg-collection-manager
bash install.sh          # sets up venv, system deps, and config.json
bash start_desktop.sh
```

The database is created automatically on first launch.

### Dependencies

| | |
|---|---|
| Python 3.10+ | |
| PyQt6, qasync | Desktop GUI |
| aiohttp, aiosqlite | Async networking and database |
| pytesseract, OpenCV | Card scanning (requires Tesseract installed on the system) |
| matplotlib | Price history and statistics charts |
| Scryfall API | Card data and prices — no key required |
| Brave Search API | Buylist web search — free key at brave.com/search/api |

Full list in `deps/requirements.txt`.

---

## Desktop App

### Navigation

| Section | Contents |
|---|---|
| **Add / Scan** | Add Card (name / set / collector number) · Webcam scanner |
| **Collection** | Collection browser · Containers · Overcount · Format Bans |
| **Decks** | Deck Builder · Deck Analysis · Improve Deck |
| **Search** | Full-text + multi-filter search |
| **Buylists** | Manual buylist matching · Brave web search · store ranking |
| **Sets** | Set completion tracker |
| **Statistics** | Totals, breakdowns, top-10 cards, value chart |
| **Logs** | Live log stream with level filter |
| **Settings** | Config, CM prices, buylist sources, backup / restore |

---

### Add Card

Three lookup modes:

- **Name search** — partial or full name, optional set filter
- **Direct lookup** — set code + collector number gives an exact single-card result
- **Language** — select the target language before searching; if no print exists in that language, the app falls back to English and stamps the chosen language on the card

**Keyboard workflow** — set code and collector number can be entered entirely without a mouse:
type set → Tab → collector number → **Enter** (search) → **Ctrl+Enter** (add).
After adding, focus returns to the collector number field with its content selected, ready for the next number.

---

### Card Scanner

Drop a card in front of the webcam. The app:

1. Isolates the card from the background (OpenCV)
2. Extracts the card name via OCR (Tesseract)
3. Resolves against Scryfall and adds to the collection

---

### Collection & Card Detail

Paginated list with text search. Multi-select rows for batch move or remove. Click any card for details:

- **EUR price** — Scryfall market price (with `~` prefix for approximate prices)
- **CM Trend / Avg30** — Cardmarket prices from the locally cached price guide
- **USD price** — Scryfall USD
- **↩ Flip** — appears for double-faced cards; loads the back-face image
- **Price History** — EUR chart for that card over time

---

### Prices

**Scryfall price fallback chain** (runs on startup for cards missing a price):

1. Own EUR price → stored directly
2. No EUR but has USD → converted via live ECB exchange rate (cached 24 h), marked `~`
3. Both null → look up the cheapest English printing for the same oracle ID, marked `~`

Approximate (`~`) prices are always re-fetched on the next startup so they self-correct.

**Cardmarket prices** — download the official CM price guide in Settings → Maintenance. Stores `Trend` and `Avg30` (and foil variants) per Cardmarket ID, shown side by side with Scryfall prices in the card detail panel.

---

### Containers

Organise cards into named binders, boxes, decks, trade piles, etc. Browse cards per container; multi-select context menu for move / remove. Toggle commander status on individual cards.

---

### Overcount

Three sub-tabs:

- **Overcounted** — cards exceeding a configurable copy threshold; filter by language
- **Sell Candidates** — cards in overcount containers above a price threshold, sorted by value
- **Bundle Builder** — create preset bundles (commons, uncommons, rares/mythics, by set) automatically split by language into separate containers

---

### Deck Builder

Builds Commander (99+1) or 60-card decks from your collection.

- **Archetype detection** — up to 3 variants (Aggro, Control, Midrange, Ramp, Tokens, Graveyard, Combo, Spellslinger, Voltron)
- **Competitive meta** — fetches top-8 decklists from MTGTop8; meta-favoured cards get a scoring bonus
- **Learns from your decks** — cards already in your deck containers are boosted; anchor cards (≥ 2 decks) are pre-selected
- **Role-slot guarantees** — always fills Ramp, Removal, Draw, Win-cons before curve-filling
- **Pip-weighted land base** — basics by mana-symbol frequency; non-basics scored from your collection
- **Power level filter** — Casual (≤ €5/card), Focused, Optimized
- **Iterative refinement** — optional hill-climbing loop
- **Deck Rating** — shown inline after every build

---

### Deck Rating

| Component | Weight | Measures |
|---|---|---|
| Synergy | 25 % | Pairwise card synergy |
| Curve fit | 25 % | Cosine similarity to archetype ideal curve |
| Role coverage | 30 % | Ramp / Removal / Draw / Board Wipes / Win-cons above format minimums |
| Coherence | 20 % | Archetype detection confidence |

Grades: **S** ≥ 90 · **A** ≥ 75 · **B** ≥ 60 · **C** ≥ 45 · **D** ≥ 30 · **F** < 30

---

### Improve Deck

1. Select any deck container
2. The app scores all non-land cards against the meta database for the deck's format
3. Weakest cards are paired with better candidates from binders/boxes (never other decks)
4. Proposals are ranked by score delta — **TIER 1** (dark green) and **TIER 2** (blue)
5. Click **Accept** → confirmation dialog → containers swapped in the database

Color identity is respected throughout.

---

### Deck Analysis

- Commander name with rendered SVG mana cost icons
- Archetype badges with confidence %, synergy and curve-fit scores
- Deck Rating badge with per-component subscores and missing-role warnings
- Mana curve chart (matplotlib, dark theme, dashed ideal-curve overlay)
- Full card table sortable by mana value, type, price

---

### Buylists

**Manual** — paste a URL or raw text; the app fetches, parses, and cross-references your collection. Shows buylist price vs. market price (green if ≥ 80 %).

**Web Search (Brave API)** — enter a keyword; the app discovers store pages automatically, parses each one, and ranks stores by total payout. Right-click a store to save its URL or store login credentials for auto-login on walled sites.

---

### Set Completion

Per-set completion bar (owned distinct cards / Scryfall total), rarity-coloured card list with language, condition, foil flag, and price. Filter by set type (Standard, Masters, Commander, Other).

---

### Statistics

- Total count, unique cards, foil split, total EUR / USD value
- Rarity breakdown — count and EUR value per tier
- Language breakdown — totals and foil split per language
- Top 10 most valuable cards — thumbnails in a 2×5 grid
- Collection value over time — EUR line chart from daily price snapshots

---

### Format Legality

Ban tracking rebuilt from Scryfall legality data on every startup. Covers Standard, Pioneer, Modern, Legacy, Vintage (restricted list), Commander. Per-card override available in the Format Bans tab.

---

## Configuration

Settings live in `config.json` (excluded from git). Created from `config.json.example` by `install.sh`. Edit in **Settings → Configuration** inside the app.

| Key | Default | Description |
|---|---|---|
| `discord.token` | — | Discord bot token |
| `app.backup_dir` | `backups` | Directory for `.db` backup files |
| `container_types` | `["binder","box","deck","commander","overcount"]` | Available container types |
| `overcount_excluded_types` | `["deck","commander","overcount"]` | Types excluded from overcount checks |
| `buylist_sources` | `[]` | Saved buylist URLs — appear as quick-select in Buylists |
| `store_credentials` | `[]` | Per-store login credentials for auto-login (stored in plaintext) |
| `brave.api_key` | — | Brave Search API key |
| `brave.keywords` | `[…]` | Default buylist search keywords |
| `brave.max_results` | `15` | Results per keyword |

---

## Discord Bot

The bot is **autoscan-only**: drop a card image into the configured channel and it scans, resolves via Scryfall, and adds the card to the collection.

### Setup

```bash
bash server/install.sh
# fill in config.json (discord.token, scan channel ID)
sudo bash server/mtg-discord-bot_service_install.sh
```

### Required permissions

OAuth2 permission integer: `117760`

| Permission | Why |
|---|---|
| View Channel | Read incoming messages |
| Send Messages | Post scan confirmations and errors |
| Embed Links | Send scan result embeds |
| Attach Files | Send scan confirmation with card image |
| Read Message History | Read image attachments from users |

Enable **Message Content Intent** and **Server Members Intent** in the Discord Developer Portal.

---

## Architecture

```
core/               Shared service layer
  database/         Async SQLite — schema, migrations, mixin query modules
  scryfall.py       Scryfall API client with rate limiting and backoff
  scanner/          Card isolation (OpenCV) + OCR (Tesseract)
  analysis.py       Archetype detection, card scoring, mana-curve optimisation, deck rating
  deckbuilder/      Synergy scoring and deck construction
  meta_crawler.py   MTGTop8 competitive meta fetcher
  brave_search.py   Brave Search API client
  buylist_parser.py Buylist parser (HTML, TSV, CSV, plain text)
  fx.py             ECB exchange rate (USD → EUR, cached 24 h)
  i18n.py           Internationalisation (_() lookup, language setup)
  image_cache.py    Local card image cache (front + back face)
  exporter.py       Moxfield CSV, full CSV, JSON
  importer.py       Moxfield CSV, full CSV, JSON

desktop/            PyQt6 desktop application
  app.py            QApplication entry point
  main_window.py    Sidebar navigation, DB init, daily price sync
  widgets/          All page widgets

server/             Discord bot
  bot.py            Autoscan bot (discord.py)
```

### Database tables

| Table / View | Purpose |
|---|---|
| `collection` | One row per physical card |
| `containers` | Named storage locations |
| `card_prices` | Scryfall EUR/USD per `scryfall_id`, with `price_approx` flag |
| `cm_prices` | Cardmarket price guide cache per `cm_id` |
| `price_history` | Daily EUR snapshots per `scryfall_id` |
| `format_bans` | Format legality per card + format |
| `meta_card_scores` | Competitive meta scores per card name and format |
| `collection_fts` | FTS5 virtual table, synced via triggers |
| `collection_with_prices` | View joining collection + card_prices + cm_prices |

WAL mode enabled. Schema migrations run automatically on startup.
