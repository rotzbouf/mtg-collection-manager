"""MTG Collection Manager — Discord bot."""

# ── Environment setup — must run before ANY library that may touch CUDA ──────
import os
import warnings
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", message=".*pin_memory.*")
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import difflib
import gzip
import io
import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import NamedTuple, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import deckbuilder
import exporter as exp
import importer as imp
import scanner
from database import Database
from scryfall import ScryfallClient

logger = logging.getLogger(__name__)


def _configure_logging(debug: bool = False) -> None:
    # Under systemd, journald adds timestamps and log level — keep the format minimal.
    # For manual terminal runs, include them so the output is self-contained.
    under_systemd = "JOURNAL_STREAM" in os.environ
    fmt = (
        "%(levelname)-8s %(name)s: %(message)s"
        if under_systemd
        else "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))
    level = logging.DEBUG if debug else logging.INFO
    logging.getLogger().setLevel(level)
    logging.getLogger().addHandler(handler)

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
SCAN_CHANNEL_ID     = int(os.getenv("DISCORD_SCAN_CHANNEL_ID",       0)) or None
DECK_CHANNEL_ID     = int(os.getenv("DISCORD_DECKBUILDER_CHANNEL_ID", 0)) or None
SHOWCASE_CHANNEL_ID = int(os.getenv("DISCORD_SHOWCASE_CHANNEL_ID",    0)) or None
SEARCH_CHANNEL_ID   = int(os.getenv("DISCORD_SEARCH_CHANNEL_ID",      0)) or None
GUEST_ROLE        = os.getenv("DISCORD_GUEST_ROLE",     "")   # read-only commands
COLLECTOR_ROLE    = os.getenv("DISCORD_COLLECTOR_ROLE", "")   # add / scan / update
ADMIN_ROLE        = os.getenv("DISCORD_ADMIN_ROLE",     "")   # remove / container mgmt / index rebuild
DEBUG_SCAN_PREVIEW = os.getenv("DEBUG_SCAN_PREVIEW", "0") == "1"  # send processed image for debugging
BACKUP_DIR = pathlib.Path(os.getenv("BACKUP_DIR", "backups"))

# Running index-build task (update or rebuild); None when idle
CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]
LANG_EMOJI = {
    "en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹",
    "es": "🇪🇸", "pt": "🇵🇹", "ja": "🇯🇵", "ko": "🇰🇷",
    "ru": "🇷🇺", "zhs": "🇨🇳", "zht": "🇹🇼",
}
COLOR_NAMES = {
    "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"
}
RARITY_COLOR = {
    "common": 0x1A1A1A,
    "uncommon": 0xC0C0C0,
    "rare": 0xD4AF37,
    "mythic": 0xFF6600,
    "special": 0x9B59B6,
    "bonus": 0x3498DB,
}


# ──────────────────────────────────────────────────────────────────────────────
# Scan helpers
# ──────────────────────────────────────────────────────────────────────────────

class _ScanMatch(NamedTuple):
    card: Optional[dict]
    detected_lang: str
    method_parts: list
    extracted_name: Optional[str]
    collector_info: dict


async def _resolve_scan(image_bytes: bytes) -> _ScanMatch:
    """Run name OCR and footer OCR concurrently and return the best card match."""
    extracted_name, collector_info = await asyncio.gather(
        asyncio.to_thread(scanner.extract_name, image_bytes),
        asyncio.to_thread(scanner.extract_collector_info, image_bytes),
    )
    collector_info = collector_info or {}

    # Collector match: set code + number → exact Scryfall lookup
    collector_card: Optional[dict] = None
    if collector_info.get("set_code") and collector_info.get("collector_number"):
        _clang = collector_info.get("language", "en")
        collector_card = await bot.scryfall.get_by_collector(
            collector_info["set_code"], collector_info["collector_number"], _clang
        )
        if not collector_card and _clang != "en":
            collector_card = await bot.scryfall.get_by_collector(
                collector_info["set_code"], collector_info["collector_number"], "en"
            )

    # OCR name match — set code hint narrows Scryfall search
    ocr_card: Optional[dict] = None
    ocr_lang = "unknown"
    if extracted_name and not collector_card:
        set_hint = collector_info.get("set_code")
        ocr_card, ocr_lang = await bot.scryfall.resolve_card(extracted_name, set_code=set_hint)
        if not ocr_card and set_hint:
            ocr_card, ocr_lang = await bot.scryfall.resolve_card(extracted_name)

    footer_lang = collector_info.get("language")

    card: Optional[dict] = None
    detected_lang = "en"
    method_parts: list[str] = []

    if collector_card:
        card = collector_card
        detected_lang = footer_lang or "en"
        set_info = f'{collector_info["set_code"]} #{collector_info["collector_number"]}'
        method_parts.append(f"collector [{set_info}]")
        if extracted_name:
            en_name = collector_card.get("name_en", "")
            de_name = collector_card.get("name_de") or collector_card.get("printed_name", "")
            ratio = max(
                difflib.SequenceMatcher(None, extracted_name.lower(), en_name.lower()).ratio(),
                difflib.SequenceMatcher(None, extracted_name.lower(), de_name.lower()).ratio() if de_name else 0,
            )
            if ratio >= 0.55:
                method_parts.append(f'name confirmed: "{extracted_name}" ({ratio:.0%})')
            else:
                logger.debug("Collector/name mismatch: OCR='%s' vs '%s' (ratio=%.2f)",
                             extracted_name, en_name, ratio)
                method_parts.append(f'OCR: "{extracted_name}" (differs {ratio:.0%})')

    elif ocr_card:
        card = ocr_card
        detected_lang = footer_lang or (ocr_lang if ocr_lang != "unknown" else "en")
        method_parts.append(f'OCR [{ocr_lang}]: "{extracted_name}"')
        if footer_lang:
            method_parts.append(f"lang: {footer_lang} (footer)")

    return _ScanMatch(
        card=card,
        detected_lang=detected_lang,
        method_parts=method_parts,
        extracted_name=extracted_name,
        collector_info=collector_info,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Role-based access control
#
# Hierarchy (each tier also grants all lower-tier permissions):
#   Admin  ≥  Collector  ≥  Guest
#
# A tier is unrestricted (open to everyone) when its role is not configured.
# ──────────────────────────────────────────────────────────────────────────────

def _member_has_any_role(member: discord.Member, *role_settings: str) -> bool:
    """True if the member holds at least one of the non-empty configured roles."""
    configured = [r for r in role_settings if r]
    if not configured:
        return True  # nothing configured → unrestricted
    member_role_ids   = {str(r.id) for r in member.roles}
    member_role_names = {r.name    for r in member.roles}
    return any(r in member_role_ids or r in member_role_names for r in configured)


async def _deny(interaction: discord.Interaction, required_role: str) -> None:
    msg = (
        f"You need the **{required_role}** role to use this command."
        if required_role
        else "You do not have permission to use this command."
    )
    await interaction.response.send_message(msg, ephemeral=True)


async def _require_role(
    interaction: discord.Interaction,
    gate_role: str,
    accepted_roles: list[str],
) -> bool:
    """Return True if the user passes the role gate. Sends a denial and returns False otherwise."""
    if not gate_role:
        return True
    if not isinstance(interaction.user, discord.Member):
        return True
    if _member_has_any_role(interaction.user, *accepted_roles):
        return True
    await _deny(interaction, gate_role)
    return False


async def require_guest(interaction: discord.Interaction) -> bool:
    """Read-only commands. Open to all when DISCORD_GUEST_ROLE is not configured."""
    return await _require_role(interaction, GUEST_ROLE, [GUEST_ROLE, COLLECTOR_ROLE, ADMIN_ROLE])


async def require_collector(interaction: discord.Interaction) -> bool:
    """Add/modify commands. Open to all when DISCORD_COLLECTOR_ROLE is not configured."""
    return await _require_role(interaction, COLLECTOR_ROLE, [COLLECTOR_ROLE, ADMIN_ROLE])


async def require_admin(interaction: discord.Interaction) -> bool:
    """Destructive/admin commands. Open to all when DISCORD_ADMIN_ROLE is not configured."""
    return await _require_role(interaction, ADMIN_ROLE, [ADMIN_ROLE])


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fmt_price(card: dict) -> str:
    parts = []
    if card.get("price_eur"):
        parts.append(f"€{card['price_eur']:.2f}")
    if card.get("price_usd"):
        parts.append(f"${card['price_usd']:.2f}")
    return " / ".join(parts) if parts else "—"


def _fmt_set(card: dict) -> str:
    name = card.get("set_name") or "?"
    code = (card.get("set_code") or "?").upper()
    nr   = card.get("collector_number") or "?"
    return f"{name} ({code}) #{nr}"


def _fmt_condition(card: dict) -> str:
    cond = card.get("condition") or "NM"
    foil = " ✨" if card.get("foil") else ""
    lang = (card.get("language") or "en").upper()
    return f"{cond}{foil} · {lang}"


def card_embed(card: dict, title_prefix: str = "") -> discord.Embed:
    rarity = (card.get("rarity") or "common").lower()
    color = RARITY_COLOR.get(rarity, 0x2ECC71)

    lang = card.get("language", "en")
    lang_flag = LANG_EMOJI.get(lang, lang.upper())

    name_en = card.get("name_en") or ""
    name_de = card.get("name_de") or ""
    title = f"{title_prefix}{name_en}"
    if name_de and name_de != name_en:
        title += f" / {name_de}"

    embed = discord.Embed(title=title, color=color)

    if card.get("image_url"):
        embed.set_thumbnail(url=card["image_url"])

    colors = card.get("colors", [])
    color_str = " ".join(COLOR_NAMES.get(c, c) for c in colors) if colors else "Colorless"

    embed.add_field(name="Type", value=card.get("type_line") or "—", inline=True)
    embed.add_field(name="Mana", value=card.get("mana_cost") or "—", inline=True)
    embed.add_field(name="CMC", value=str(card.get("cmc", 0)), inline=True)
    embed.add_field(name="Color", value=color_str, inline=True)
    embed.add_field(name="Set", value=f"{card.get('set_name', '?')} ({(card.get('set_code') or '?').upper()})", inline=True)
    embed.add_field(name="№", value=card.get("collector_number") or "—", inline=True)
    embed.add_field(name="Rarity", value=rarity.capitalize(), inline=True)
    embed.add_field(name="Language", value=lang_flag, inline=True)

    cond = card.get("condition", "NM")
    foil = " ✨" if card.get("foil") else ""
    container = card.get("container_name") or "—"
    embed.add_field(name="Condition", value=f"{cond}{foil}", inline=True)
    embed.add_field(name="Container", value=container, inline=True)

    embed.add_field(name="Price", value=_fmt_price(card), inline=True)

    if card.get("oracle_text"):
        oracle = card["oracle_text"]
        if len(oracle) > 400:
            oracle = oracle[:397] + "…"
        embed.add_field(name="Text", value=oracle, inline=False)

    if card.get("flavor_text"):
        embed.add_field(name="Flavor", value=f"*{card['flavor_text'][:200]}*", inline=False)

    if card.get("id"):
        embed.set_footer(text=f"ID: {card['id']}")

    return embed


def paginate_embeds(
    cards: list[dict], page: int, per_page: int = 10, total: int = 0
) -> tuple[discord.Embed, int]:
    """Build a paginated collection embed.

    When *total* is provided, *cards* is treated as the already-sliced page
    and total/pages are computed from the given true count.  Without it the
    function slices *cards* itself (legacy behaviour).
    """
    if total:
        pages = max(1, (total + per_page - 1) // per_page)
        chunk = cards
    else:
        total = len(cards)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        start = (page - 1) * per_page
        chunk = cards[start:start + per_page]

    embed = discord.Embed(
        title=f"Collection — page {page}/{pages}  ({total} entries)",
        color=0x2ECC71,
    )
    for c in chunk:
        name = c.get("name_en", "?")
        if c.get("name_de") and c["name_de"] != name:
            name += f" / {c['name_de']}"
        lang_flag = LANG_EMOJI.get(c.get("language", "en"), "")
        foil = " ✨" if c.get("foil") else ""
        container = c.get("container_name") or "—"
        price = f"€{c['price_eur']:.2f}" if c.get("price_eur") else "—"
        val = (
            f"{c.get('set_code', '').upper()} #{c.get('collector_number', '?')} "
            f"| {c.get('condition', 'NM')}{foil} "
            f"| {price} | {lang_flag} | 📦 {container}"
        )
        embed.add_field(name=f"[{c.get('id', '?')}] {name}", value=val, inline=False)

    return embed, pages


# ──────────────────────────────────────────────────────────────────────────────
# Bot
# ──────────────────────────────────────────────────────────────────────────────

_READ_ONLY_COMMANDS = {
    "search", "list", "stats", "export",
    "container list", "showcase",
}

class MTGCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cmd = interaction.command
        if cmd is None:
            return True
        name = cmd.qualified_name
        if name.startswith("deck"):
            if DECK_CHANNEL_ID and interaction.channel_id != DECK_CHANNEL_ID:
                await interaction.response.send_message(
                    f"Deck commands only work in <#{DECK_CHANNEL_ID}>.", ephemeral=True
                )
                return False
        elif name == "search":
            if SEARCH_CHANNEL_ID and interaction.channel_id != SEARCH_CHANNEL_ID:
                await interaction.response.send_message(
                    f"Search only works in <#{SEARCH_CHANNEL_ID}>.", ephemeral=True
                )
                return False
        elif name not in _READ_ONLY_COMMANDS:
            if SCAN_CHANNEL_ID and interaction.channel_id != SCAN_CHANNEL_ID:
                await interaction.response.send_message(
                    f"This command only works in <#{SCAN_CHANNEL_ID}>.", ephemeral=True
                )
                return False
        return True


class MTGBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, tree_cls=MTGCommandTree)
        self.db = Database()
        self.scryfall = ScryfallClient()

    async def setup_hook(self):
        await self.db.initialize()

        # Sync commands before anything slow (OCR model load can take minutes on first run)
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            try:
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced to guild %s", GUILD_ID)
            except Exception as e:
                logger.warning("Guild sync failed (%s) — falling back to global sync", e)
                await self.tree.sync()
                logger.info("Slash commands synced globally (may take up to 1 hour to appear)")
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to 1 hour to appear)")

        record_prices_task.start()
        refresh_null_prices_task.start()
        # Load OCR models in background — slow on first run, must not block the sync
        asyncio.create_task(self._init_ocr())

    async def _init_ocr(self):
        try:
            await scanner.init_ocr()
        except Exception as e:
            logger.error("OCR init failed: %s", e)

    async def close(self):
        await self.db.close()
        await self.scryfall.close()
        await super().close()

    async def on_ready(self):
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name="📦 MTG Collection"))


bot = MTGBot()


@tasks.loop(hours=24)
async def record_prices_task():
    try:
        n = await bot.db.record_prices()
        logger.info("Price snapshot recorded for %d unique cards", n)
    except Exception as e:
        logger.error("Price recording failed: %s", e)


@record_prices_task.before_loop
async def _before_record_prices():
    await bot.wait_until_ready()


@tasks.loop(hours=24)
async def refresh_null_prices_task():
    """Re-fetch prices from Scryfall for collection entries that have no EUR price."""
    try:
        cards = await bot.db.get_null_price_cards()
        if not cards:
            logger.info("Null-price refresh: all cards have a price, nothing to do")
            return
        logger.info("Null-price refresh: %d card(s) missing EUR price", len(cards))
        updated = 0
        for entry in cards:
            scryfall_id = entry["scryfall_id"]
            card = await bot.scryfall.get_by_id(scryfall_id)
            price_eur = card.get("price_eur") if card else None
            price_usd = card.get("price_usd") if card else None

            # Fallback: look up the EN oracle card by name
            if not price_eur and entry.get("name_en"):
                en_card = await bot.scryfall.get_by_name(entry["name_en"], fuzzy=False)
                if en_card:
                    price_eur = price_eur or en_card.get("price_eur")
                    price_usd = price_usd or en_card.get("price_usd")

            if price_eur or price_usd:
                await bot.db.update_card_prices(scryfall_id, price_eur, price_usd)
                updated += 1

        logger.info("Null-price refresh: updated %d/%d card(s)", updated, len(cards))
    except Exception as e:
        logger.error("Null-price refresh failed: %s", e)


@refresh_null_prices_task.before_loop
async def _before_refresh_null_prices():
    await bot.wait_until_ready()
    await asyncio.sleep(60)  # stagger after record_prices_task


# ──────────────────────────────────────────────────────────────────────────────
# Container autocomplete
# ──────────────────────────────────────────────────────────────────────────────

async def container_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    containers = await bot.db.list_containers()
    return [
        app_commands.Choice(name=f"{c['name']} ({c['card_count']} cards)", value=str(c["id"]))
        for c in containers
        if current.lower() in c["name"].lower()
    ][:25]


# ──────────────────────────────────────────────────────────────────────────────
# /container  (create / list / delete / rename)
# ──────────────────────────────────────────────────────────────────────────────

container_group = app_commands.Group(name="container", description="Manage card containers (binders, boxes, …)")

CONTAINER_TYPES = ["binder", "box", "deck", "trade", "other"]


class _ContainerListView(discord.ui.View):
    """Attaches Browse and New Container buttons to the /container list response."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Browse", emoji="📦", style=discord.ButtonStyle.primary)
    async def browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_guest(interaction):
            return
        containers = await bot.db.list_containers()
        view = BrowseContainersView(containers)
        await interaction.response.send_message(
            "Select a container to browse:", view=view, ephemeral=True
        )

    @discord.ui.button(label="New Container", emoji="➕", style=discord.ButtonStyle.secondary)
    async def new_container(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        await interaction.response.send_modal(ContainerCreateModal(refresh_browse=False))


@container_group.command(name="list", description="List all containers")
async def container_list(interaction: discord.Interaction):
    if not await require_guest(interaction):
        return
    containers = await bot.db.list_containers()
    if not containers:
        view = _ContainerListView()
        await interaction.response.send_message(
            "No containers yet. Create your first one:", view=view, ephemeral=True
        )
        return
    total_value = sum(c.get("total_value_eur") or 0 for c in containers)
    embed = discord.Embed(
        title=f"Containers  —  total collection value: €{total_value:.2f}",
        color=0x3498DB,
    )
    for c in containers:
        value_str = f"€{c['total_value_eur']:.2f}" if c.get("total_value_eur") else "€0.00"
        val = f"`{c['type']}` | {c['card_count']} card(s) | {value_str}"
        if c.get("description"):
            val += f"\n{c['description']}"
        embed.add_field(name=f"[{c['id']}] {c['name']}", value=val, inline=False)
    await interaction.response.send_message(embed=embed, view=_ContainerListView())


@container_group.command(name="move", description="Move all cards from one container to another")
@app_commands.describe(source="Source container", destination="Destination container")
@app_commands.autocomplete(source=container_autocomplete, destination=container_autocomplete)
async def container_move(interaction: discord.Interaction, source: str, destination: str):
    if not await require_collector(interaction):
        return
    try:
        src_id, dst_id = int(source), int(destination)
    except ValueError:
        await interaction.response.send_message("Invalid container selection.", ephemeral=True)
        return
    if src_id == dst_id:
        await interaction.response.send_message("Source and destination must be different.", ephemeral=True)
        return
    src = await bot.db.get_container(src_id)
    dst = await bot.db.get_container(dst_id)
    if not src or not dst:
        await interaction.response.send_message("Container not found.", ephemeral=True)
        return
    count = await bot.db.count_cards(container_id=src_id)
    if count == 0:
        await interaction.response.send_message(f"📦 **{src['name']}** is empty.", ephemeral=True)
        return
    view = _ContainerMoveConfirmView(src_id, src["name"], dst_id, dst["name"], count)
    await interaction.response.send_message(
        f"Move **{count}** card(s) from 📦 **{src['name']}** → 📦 **{dst['name']}**?",
        view=view, ephemeral=True,
    )


class _ContainerMoveConfirmView(discord.ui.View):
    def __init__(self, src_id: int, src_name: str, dst_id: int, dst_name: str, count: int):
        super().__init__(timeout=60)
        self._src_id   = src_id
        self._src_name = src_name
        self._dst_id   = dst_id
        self._dst_name = dst_name
        self._count    = count

    @discord.ui.button(label="Move", style=discord.ButtonStyle.primary, emoji="📦")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        cards = await bot.db.list_cards(limit=self._count + 100, container_id=self._src_id)
        card_ids = [c["id"] for c in cards]
        n = await bot.db.move_cards_to_container(card_ids, self._dst_id)
        await interaction.response.edit_message(
            content=f"Moved **{n}** card(s) from 📦 **{self._src_name}** to 📦 **{self._dst_name}**.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


bot.tree.add_command(container_group)


class _AddAnotherView(discord.ui.View):
    """Shown after /add — lets the user add one more copy of the same card."""
    def __init__(self, card: dict, added_by: str, count: int, base_desc: str):
        super().__init__(timeout=120)
        self._card = card
        self._added_by = added_by
        self._count = count
        self._base_desc = base_desc

    @discord.ui.button(label="➕ Add Another Copy", style=discord.ButtonStyle.success)
    async def add_another(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        new_id = await bot.db.add_card(self._card, added_by=self._added_by)
        self._count += 1
        embed = card_embed(self._card, title_prefix="Added ✅  ")
        n = self._count - 1
        embed.description = self._base_desc + f"\n➕ +{n} additional cop{'y' if n == 1 else 'ies'} added (last ID: **{new_id}**)"
        await interaction.response.edit_message(embed=embed, view=self)


# ──────────────────────────────────────────────────────────────────────────────
# /add
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="add", description="Add a card to your collection by name")
@app_commands.describe(
    name="Card name (English or German)",
    container="Container to put the card in",
    set_code="Set code, e.g. 'MH3' (optional)",
    language="Override detected language (en/de)",
    condition="Card condition: NM, LP, MP, HP, DMG",
    foil="Is this a foil?",
    quantity="How many copies",
    notes="Personal notes",
)
@app_commands.autocomplete(container=container_autocomplete)
@app_commands.choices(condition=[app_commands.Choice(name=c, value=c) for c in CONDITIONS])
@app_commands.choices(language=[
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="German / Deutsch", value="de"),
])
async def cmd_add(
    interaction: discord.Interaction,
    name: str,
    container: str = "",
    set_code: str = "",
    language: str = "",
    condition: str = "NM",
    foil: bool = False,
    quantity: int = 1,
    notes: str = "",
):
    if not await require_collector(interaction):
        return
    await interaction.response.defer(thinking=True)

    card, detected_lang = await bot.scryfall.resolve_card(name, set_code or None)
    if not card:
        await interaction.followup.send(f"Card **{name}** not found on Scryfall.", ephemeral=True)
        return

    card["language"] = language or detected_lang or "en"
    card["condition"] = condition
    card["foil"] = foil
    card["quantity"] = 1
    card["notes"] = notes or None

    # Resolve container
    container_id = None
    container_name = None
    if container:
        if container.isdigit():
            container_id = int(container)
            c = await bot.db.get_container(container_id)
            if not c:
                await interaction.followup.send(
                    f"No container with ID **{container}** found. Use `/container list` to see available containers.",
                    ephemeral=True,
                )
                return
            container_name = c["name"]
        else:
            existing = await bot.db.list_containers()
            match = next((c for c in existing if c["name"].lower() == container.lower()), None)
            if match:
                container_id = match["id"]
                container_name = match["name"]
            else:
                container_id = await bot.db.create_container(container)
                container_name = container
    card["container_id"] = container_id
    card["container_name"] = container_name

    copies = max(1, quantity)
    ids = []
    for _ in range(copies):
        ids.append(await bot.db.add_card(card, added_by=str(interaction.user.id)))

    card["id"] = ids[0]
    lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
    new_tag = "  *(container created)*" if container and not container.isdigit() and container_name == container else ""
    id_range = f"IDs **{ids[0]}–{ids[-1]}**" if len(ids) > 1 else f"ID **{ids[0]}**"
    desc = f"Saved as {id_range} ({len(ids)} cop{'y' if len(ids)==1 else 'ies'}) | Language {lang_flag}{new_tag}"
    embed = card_embed(card, title_prefix="Added ✅  ")
    embed.description = desc
    view = _AddAnotherView(card, str(interaction.user.id), len(ids), desc)
    await interaction.followup.send(embed=embed, view=view)


# ──────────────────────────────────────────────────────────────────────────────
# Paginated list / search views
# ──────────────────────────────────────────────────────────────────────────────

_LIST_PER_PAGE = 10
_SEARCH_PER_PAGE = 10


def _nav_buttons(view: discord.ui.View, page: int, pages: int,
                 prev_cb, next_cb, *, row: int = 0) -> None:
    """Add ◀ [page/pages] ▶ buttons to a view."""
    if page > 1:
        btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=row)
        btn.callback = prev_cb
        view.add_item(btn)
    indicator = discord.ui.Button(
        label=f"{page} / {pages}", style=discord.ButtonStyle.secondary,
        disabled=True, row=row,
    )
    view.add_item(indicator)
    if page < pages:
        btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=row)
        btn.callback = next_cb
        view.add_item(btn)


def _card_select_label(c: dict) -> str:
    name = (c.get("printed_name") or c.get("name_en") or "Unknown")[:80]
    foil = " ✨" if c.get("foil") else ""
    lang = f" [{(c.get('language') or 'en').upper()}]" if c.get("language") != "en" else ""
    return f"{name}{foil}{lang}"[:100]


def _card_select_desc(c: dict) -> str:
    parts = [(c.get("set_code") or "?").upper(), c.get("condition", "NM")]
    if c.get("price_eur"):
        parts.append(f"€{c['price_eur']:.2f}")
    if c.get("container_name"):
        parts.append(f"📦 {c['container_name']}")
    return "  ·  ".join(parts)[:100]


def _add_card_select(view: discord.ui.View, cards: list[dict], row: int = 0) -> None:
    """Add a card picker Select to a view. Selecting opens the card manage panel."""
    if not cards:
        return
    options = [
        discord.SelectOption(
            label=_card_select_label(c),
            value=str(c["id"]),
            description=_card_select_desc(c),
        )
        for c in cards[:25]
    ]
    sel = discord.ui.Select(placeholder="Open card…", options=options, row=row)

    async def _on_card(interaction: discord.Interaction):
        card_id = int(interaction.data["values"][0])
        card = await bot.db.get_card(card_id)
        if not card:
            await interaction.response.send_message("Card not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=_card_manage_embed(card),
            view=CardManageView(card, None, 0),
            ephemeral=True,
        )

    sel.callback = _on_card
    view.add_item(sel)


class ListPageView(discord.ui.View):
    def __init__(self, page: int, pages: int, total: int,
                 sort: str, language: str, cards: list[dict]):
        super().__init__(timeout=300)
        self._page = page
        self._pages = pages
        self._total = total
        self._sort = sort
        self._language = language
        _add_card_select(self, cards, row=0)
        _nav_buttons(self, page, pages, self._prev, self._next, row=1)

    async def _go(self, interaction: discord.Interaction, page: int):
        cards = await bot.db.list_cards(
            limit=_LIST_PER_PAGE,
            offset=(page - 1) * _LIST_PER_PAGE,
            sort=self._sort,
            language=self._language or None,
        )
        embed, _ = paginate_embeds(cards, page, per_page=_LIST_PER_PAGE, total=self._total)
        view = ListPageView(page, self._pages, self._total, self._sort, self._language, cards)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev(self, interaction: discord.Interaction):
        await self._go(interaction, self._page - 1)

    async def _next(self, interaction: discord.Interaction):
        await self._go(interaction, self._page + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class SearchPageView(discord.ui.View):
    def __init__(self, query: str, page: int, pages: int, total: int, cards: list[dict]):
        super().__init__(timeout=300)
        self._query = query
        self._page = page
        self._pages = pages
        self._total = total
        self._cache: dict[int, list[dict]] = {page: cards}
        _add_card_select(self, cards, row=0)
        _nav_buttons(self, page, pages, self._prev, self._next, row=1)

    async def _go(self, interaction: discord.Interaction, page: int):
        if page not in self._cache:
            results = await bot.db.search(
                self._query, limit=_SEARCH_PER_PAGE,
                offset=(page - 1) * _SEARCH_PER_PAGE,
            )
            self._cache[page] = results
        else:
            results = self._cache[page]
        embed, _ = paginate_embeds(results, page, per_page=_SEARCH_PER_PAGE, total=self._total)
        embed.title = f'Search: "{self._query}"  —  {self._total} result(s)'
        view = SearchPageView(self._query, page, self._pages, self._total, results)
        view._cache = self._cache  # forward the cache to the new view
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev(self, interaction: discord.Interaction):
        await self._go(interaction, self._page - 1)

    async def _next(self, interaction: discord.Interaction):
        await self._go(interaction, self._page + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# /search
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="search", description="Full-text search across all card fields")
@app_commands.describe(query="Search terms (name, type, oracle text, set, …)")
async def cmd_search(interaction: discord.Interaction, query: str):
    if not await require_guest(interaction):
        return
    query = query.strip()
    if not query:
        await interaction.response.send_message("Please enter a search term.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    total = await bot.db.count_search(query)
    if not total:
        await interaction.followup.send(f"No results for **{query}**.", ephemeral=True)
        return
    pages = max(1, (total + _SEARCH_PER_PAGE - 1) // _SEARCH_PER_PAGE)
    results = await bot.db.search(query, limit=_SEARCH_PER_PAGE, offset=0)
    embed, _ = paginate_embeds(results, 1, per_page=_SEARCH_PER_PAGE, total=total)
    embed.title = f'Search: "{query}"  —  {total} result(s)'
    view = SearchPageView(query, 1, pages, total, results)
    await interaction.followup.send(embed=embed, view=view)


# ──────────────────────────────────────────────────────────────────────────────
# /list
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="list", description="List your collection")
@app_commands.describe(
    page="Page number",
    sort="Sort order",
    language="Filter by language",
)
@app_commands.choices(sort=[
    app_commands.Choice(name="Chaos (default)", value="chaos"),
    app_commands.Choice(name="Name A-Z", value="name"),
    app_commands.Choice(name="Set + collector №", value="set"),
    app_commands.Choice(name="CMC", value="cmc"),
    app_commands.Choice(name="Recently added", value="added"),
])
@app_commands.choices(language=[
    app_commands.Choice(name="All", value=""),
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="German / Deutsch", value="de"),
])
async def cmd_list(
    interaction: discord.Interaction,
    page: int = 1,
    sort: str = "chaos",
    language: str = "",
):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    total = await bot.db.count_cards(language=language or None)
    if not total:
        await interaction.followup.send("Your collection is empty.", ephemeral=True)
        return
    pages = max(1, (total + _LIST_PER_PAGE - 1) // _LIST_PER_PAGE)
    page = max(1, min(page, pages))
    cards = await bot.db.list_cards(
        limit=_LIST_PER_PAGE,
        offset=(page - 1) * _LIST_PER_PAGE,
        sort=sort,
        language=language or None,
    )
    embed, _ = paginate_embeds(cards, page, per_page=_LIST_PER_PAGE, total=total)
    view = ListPageView(page, pages, total, sort, language, cards)
    await interaction.followup.send(embed=embed, view=view)


# ──────────────────────────────────────────────────────────────────────────────
# /browse — interactive container & card manager
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="browse", description="Browse containers and manage cards interactively")
async def cmd_browse(interaction: discord.Interaction):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(ephemeral=True)
    containers = await bot.db.list_containers()
    if not containers:
        view = BrowseContainersView(containers)
        await interaction.edit_original_response(
            content="No containers yet. Create your first one:", view=view
        )
        return
    view = BrowseContainersView(containers)
    await interaction.edit_original_response(content="Select a container to browse:", view=view)


# ──────────────────────────────────────────────────────────────────────────────
# /resync — re-fetch Scryfall data for one or all cards  (admin only)
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="resync", description="Re-fetch card data from Scryfall (admin only)")
@app_commands.describe(id="Collection ID to resync (omit to resync all cards)")
async def cmd_resync(interaction: discord.Interaction, id: Optional[int] = None):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    if id is not None:
        # Single card
        card_row = await bot.db.get_card(id)
        if not card_row:
            await interaction.edit_original_response(content=f"No card with ID {id}.")
            return
        scryfall_id = card_row.get("scryfall_id")
        if not scryfall_id:
            await interaction.edit_original_response(content="Card has no Scryfall ID, cannot resync.")
            return
        await interaction.edit_original_response(content=f"Fetching **{card_row['name_en']}** from Scryfall...")
        fresh = await bot.scryfall.get_by_id(scryfall_id)
        if not fresh:
            await interaction.edit_original_response(content="Scryfall returned no data for this card.")
            return
        rows = await bot.db.resync_card(scryfall_id, fresh)
        await interaction.edit_original_response(
            content=f"Resynced **{fresh['name_en']}** — {rows} collection row(s) updated."
        )
        return

    # All cards
    scryfall_ids = await bot.db.get_distinct_scryfall_ids()
    total = len(scryfall_ids)
    if not total:
        await interaction.edit_original_response(content="Collection is empty.")
        return

    await interaction.edit_original_response(content=f"Resyncing {total} unique cards from Scryfall...")
    _resync_sem = asyncio.Semaphore(10)

    async def _fetch_one(sid: str) -> tuple[str, Optional[dict]]:
        async with _resync_sem:
            return sid, await bot.scryfall.get_by_id(sid)

    tasks = [asyncio.create_task(_fetch_one(sid)) for sid in scryfall_ids]
    updated_cards = 0
    failed = 0
    for i, task in enumerate(asyncio.as_completed(tasks), 1):
        sid, fresh = await task
        if fresh:
            await bot.db.resync_card(sid, fresh)
            updated_cards += 1
        else:
            failed += 1
        if i % 25 == 0:
            await interaction.edit_original_response(
                content=f"Resyncing… {i}/{total} done ({updated_cards} updated, {failed} failed)"
            )

    summary = f"Resync complete — {updated_cards}/{total} cards updated."
    if failed:
        summary += f" {failed} card(s) could not be fetched from Scryfall."
    await interaction.edit_original_response(content=summary)


# ──────────────────────────────────────────────────────────────────────────────
# /export
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="export", description="Export your entire collection")
@app_commands.describe(format="File format")
@app_commands.choices(format=[
    app_commands.Choice(name="Moxfield CSV (recommended)", value="moxfield"),
    app_commands.Choice(name="CSV (Excel-compatible, full data)", value="csv"),
    app_commands.Choice(name="JSON", value="json"),
])
async def cmd_export(interaction: discord.Interaction, format: str = "moxfield"):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    cards = await bot.db.get_all()
    if not cards:
        await interaction.followup.send("Your collection is empty.", ephemeral=True)
        return

    if format == "moxfield":
        content = exp.to_moxfield(cards).encode("utf-8")
        filename = "collection_moxfield.csv"
    elif format == "json":
        content = exp.to_json(cards).encode("utf-8")
        filename = "collection.json"
    else:
        content = exp.to_csv(cards).encode("utf-8")
        filename = "collection.csv"

    file = discord.File(io.BytesIO(content), filename=filename)
    await interaction.followup.send(
        f"Exported **{len(cards)}** entries as `{filename}`.",
        file=file,
    )


# ──────────────────────────────────────────────────────────────────────────────
# /import
# ──────────────────────────────────────────────────────────────────────────────

async def _get_or_create_container(name: str) -> int:
    containers = await bot.db.list_containers()
    match = next((c for c in containers if c["name"].lower() == name.lower()), None)
    if match:
        return match["id"]
    return await bot.db.create_container(name)


class ImportConfirmView(discord.ui.View):
    def __init__(self, rows: list[dict], fmt: str, container_id: Optional[int], added_by: str):
        super().__init__(timeout=120)
        self._rows = rows
        self._fmt = fmt
        self._container_id = container_id
        self._added_by = added_by

    @discord.ui.button(label="Import", style=discord.ButtonStyle.success, emoji="📥")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()

        if self._fmt == "moxfield_csv":
            total = sum(r["count"] for r in self._rows)
        else:
            total = len(self._rows)

        added = skipped = 0
        await interaction.edit_original_response(content=f"Importing… 0 / {total}", view=None)

        failed_names: list[str] = []
        if self._fmt == "moxfield_csv":
            for row in self._rows:
                card = None
                if row["collector_number"] and row["set_code"]:
                    card = await bot.scryfall.get_by_collector(
                        row["set_code"], row["collector_number"], row["language"]
                    )
                    # Fallback: EN print (prices are there even for DE cards)
                    if not card and row["language"] != "en":
                        card = await bot.scryfall.get_by_collector(
                            row["set_code"], row["collector_number"], "en"
                        )
                if not card:
                    card = await bot.scryfall.get_by_name(row["name"], fuzzy=True, set_code=row["set_code"])
                if not card:
                    failed_names.append(row["name"])
                    skipped += row["count"]
                    continue
                card["condition"] = row["condition"]
                card["language"] = row["language"]
                card["foil"] = 1 if row["foil"] else 0
                card["container_id"] = self._container_id
                for _ in range(row["count"]):
                    await bot.db.add_card(card, added_by=self._added_by)
                    added += 1
                if added % 10 == 0 or skipped:
                    await interaction.edit_original_response(
                        content=f"Importing… {added + skipped} / {total}"
                    )
        else:
            failed_reasons: list[str] = []
            for row in self._rows:
                try:
                    card, container_name = imp.normalize_row(row)
                    if container_name:
                        card["container_id"] = await _get_or_create_container(container_name)
                    await bot.db.add_card(card, added_by=self._added_by)
                    added += 1
                except Exception as exc:
                    logger.warning("Import: skipped row — %s", exc)
                    failed_reasons.append(f"{row.get('name_en', '?')}: {exc}")
                    skipped += 1
                if (added + skipped) % 10 == 0:
                    await interaction.edit_original_response(
                        content=f"Importing… {added + skipped} / {total}"
                    )

        msg = f"Import complete — **{added}** card(s) added."
        if skipped:
            msg += f" **{skipped}** could not be resolved and were skipped."

        all_failures = failed_names if self._fmt == "moxfield_csv" else failed_reasons
        if all_failures:
            lines = "\n".join(f"• {f}" for f in all_failures[:10])
            if len(all_failures) > 10:
                lines += f"\n… and {len(all_failures) - 10} more"
            msg += f"\n\nFailed imports:\n{lines}"

        await interaction.edit_original_response(content=msg)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Import cancelled.", view=None)


@bot.tree.command(name="import", description="Import cards from a Moxfield CSV or bot export file")
@app_commands.describe(
    file="Moxfield CSV, bot full CSV export, or bot JSON export",
    container="Assign all cards to this container (Moxfield CSV only; leave blank to keep original containers)",
)
@app_commands.autocomplete(container=container_autocomplete)
async def cmd_import(
    interaction: discord.Interaction,
    file: discord.Attachment,
    container: Optional[str] = None,
):
    if not await require_collector(interaction):
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    await interaction.edit_original_response(content="Reading file…")

    raw = await file.read()
    try:
        fmt = imp.detect_format(file.filename, raw)
    except ValueError as exc:
        await interaction.edit_original_response(content=str(exc))
        return

    await interaction.edit_original_response(content="Parsing…")
    try:
        if fmt == "moxfield_csv":
            rows = imp.parse_moxfield_csv(raw)
        elif fmt == "full_csv":
            rows = imp.parse_full_csv(raw)
        else:
            rows = imp.parse_json(raw)
    except Exception as exc:
        await interaction.edit_original_response(content=f"Could not parse file: {exc}")
        return

    if not rows:
        await interaction.edit_original_response(content="No cards found in the file.")
        return

    # Resolve target container (Moxfield CSV only)
    container_id: Optional[int] = None
    container_label = ""
    if container and fmt == "moxfield_csv":
        container_id = await _get_or_create_container(container)
        containers = await bot.db.list_containers()
        c = next((c for c in containers if c["id"] == container_id), None)
        container_label = f" into 📦 **{c['name']}**" if c else ""

    total_cards = sum(r["count"] for r in rows) if fmt == "moxfield_csv" else len(rows)
    unique_rows = len(rows)

    lines = [
        f"**{unique_rows}** unique entr{'y' if unique_rows == 1 else 'ies'} → **{total_cards}** card(s) to import{container_label}",
        f"Format: `{fmt}`",
    ]
    if fmt == "moxfield_csv":
        lines.append("Each card will be looked up on Scryfall — large imports may take a moment.")

    view = ImportConfirmView(rows, fmt, container_id, interaction.user.display_name)
    await interaction.edit_original_response(content="\n".join(lines), view=view)


# ──────────────────────────────────────────────────────────────────────────────
# /stats
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="stats", description="Show collection statistics")
async def cmd_stats(interaction: discord.Interaction):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    s = await bot.db.stats()
    if not s:
        await interaction.followup.send("Your collection is empty.", ephemeral=True)
        return

    def _eur(v) -> str:
        return f"€{(v or 0):.2f}"

    def _n(v) -> int:
        return v or 0

    total_eur = s.get("total_value_eur") or 0
    total_usd = s.get("total_value_usd") or 0
    value_str = f"€{total_eur:.2f}" + (f"  /  ${total_usd:.2f}" if total_usd else "")

    embed = discord.Embed(title="Collection Stats", color=0x3498DB)

    # ── Overview ──
    embed.add_field(name="Total cards",  value=str(_n(s.get("total_cards"))),  inline=True)
    embed.add_field(name="Unique cards", value=str(_n(s.get("unique_cards"))), inline=True)
    embed.add_field(name="Total value",    value=value_str,                      inline=False)

    # ── English ──
    en_nf, en_f = _n(s.get("en_nonfoil")), _n(s.get("en_foil"))
    en_nf_eur, en_f_eur = s.get("en_nonfoil_eur") or 0, s.get("en_foil_eur") or 0
    embed.add_field(
        name="🇬🇧 English",
        value=(
            f"**{_n(s.get('en_total'))} cards**  —  {_eur(en_nf_eur + en_f_eur)}\n"
            f"Non-foil: {en_nf}  ({_eur(en_nf_eur)})\n"
            f"✨ Foil:  {en_f}  ({_eur(en_f_eur)})"
        ),
        inline=True,
    )

    # ── German ──
    de_nf, de_f = _n(s.get("de_nonfoil")), _n(s.get("de_foil"))
    de_nf_eur, de_f_eur = s.get("de_nonfoil_eur") or 0, s.get("de_foil_eur") or 0
    embed.add_field(
        name="🇩🇪 German",
        value=(
            f"**{_n(s.get('de_total'))} cards**  —  {_eur(de_nf_eur + de_f_eur)}\n"
            f"Non-foil: {de_nf}  ({_eur(de_nf_eur)})\n"
            f"✨ Foil:  {de_f}  ({_eur(de_f_eur)})"
        ),
        inline=True,
    )

    embed.add_field(name="​", value="​", inline=False)  # row break

    # ── Rarity ──
    rarity_lines = []
    for label, key_n, key_eur in (
        ("⬛ Common",    "r_common",   "r_common_eur"),
        ("🔘 Uncommon",  "r_uncommon", "r_uncommon_eur"),
        ("🟡 Rare",      "r_rare",     "r_rare_eur"),
        ("🟠 Mythic",    "r_mythic",   "r_mythic_eur"),
    ):
        n = _n(s.get(key_n))
        v = s.get(key_eur) or 0
        rarity_lines.append(f"{label}: **{n}**  ({_eur(v)})")
    embed.add_field(name="Rarity breakdown", value="\n".join(rarity_lines), inline=False)

    embed.add_field(name="​", value="​", inline=False)  # row break

    # ── Top 5 by value ──
    top = s.get("top_cards") or []
    if top:
        lines = []
        for i, c in enumerate(top, 1):
            name = c.get("name_en") or "?"
            foil_tag = " ✨" if c.get("foil") else ""
            lang = LANG_EMOJI.get(c.get("language", "en"), "")
            container = c.get("container_name") or "—"
            lines.append(f"{i}. {name}{foil_tag} {lang}  —  {_eur(c.get('price_eur'))}  📦 {container}")
        embed.add_field(name="Most valuable cards", value="\n".join(lines), inline=False)

    # ── Containers ──
    ct_stats = await bot.db.container_stats()
    if ct_stats:
        lines = []
        bulk_threshold = 0.05
        for ct in ct_stats:
            count = ct["card_count"]
            is_bulk = count > 0 and (ct["max_card_eur"] or 0) <= bulk_threshold
            bulk_tag = "  🗂️ BULK" if is_bulk else ""
            icon = "🗂️" if is_bulk else "📦"
            lines.append(
                f"{icon} **{ct['name']}** `{ct['type']}`  —  "
                f"{count} cards  •  {_eur(ct['total_value_eur'])}{bulk_tag}"
            )
        # Discord field limit: 1024 chars — truncate gracefully
        text = "\n".join(lines)
        if len(text) > 1020:
            text = text[:1017] + "…"
        embed.add_field(name="Containers", value=text, inline=False)

    view = StatsView()
    await interaction.followup.send(embed=embed, view=view)


class StatsView(discord.ui.View):
    """Attached to /stats — provides quick actions like viewing overcounted cards."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Overcounted Cards", emoji="⚠️", style=discord.ButtonStyle.secondary)
    async def btn_overcount(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        cards = await bot.db.get_overcount_cards(threshold=4)
        if not cards:
            await interaction.followup.send(
                "No card appears more than 4 times in your collection.", ephemeral=True
            )
            return
        containers = await bot.db.list_containers()
        embed = _overcount_summary_embed(cards)
        view = OvercountView(cards, containers)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# Cards with more than 4 copies — helper functions and views
# ──────────────────────────────────────────────────────────────────────────────

def _overcount_card_line(card: dict, threshold: int = 4) -> str:
    """One summary line for an overcounted card group."""
    name = card.get("printed_name") or card.get("name_de") or card["name_en"]
    if name != card["name_en"]:
        name = f"{name} ({card['name_en']})"
    total = card["total"]
    excess = total - threshold

    # Per-container count
    container_counts: dict[str, int] = {}
    for e in card["entries"]:
        label = e.get("container_name") or "_(no container)_"
        container_counts[label] = container_counts.get(label, 0) + 1
    container_str = "  ·  ".join(
        f"📦 {cn}: {cnt}×" if cn != "_(no container)_" else f"{cn}: {cnt}×"
        for cn, cnt in container_counts.items()
    )

    # Price stats
    prices = [e["price_eur"] for e in card["entries"] if e.get("price_eur")]
    price_str = ""
    if prices:
        lo, hi = min(prices), max(prices)
        total_val = sum(prices)
        if lo == hi:
            price_str = f"€{lo:.2f}/ea  ·  Σ €{total_val:.2f}"
        else:
            price_str = f"€{lo:.2f}–€{hi:.2f}  ·  Σ €{total_val:.2f}"

    line = f"**{name}** — {total}×  _(+{excess} excess)_\n  {container_str}"
    if price_str:
        line += f"\n  {price_str}"
    return line


def _overcount_summary_embed(cards: list[dict], threshold: int = 4) -> discord.Embed:
    lines = [_overcount_card_line(c, threshold) for c in cards]
    chunks: list[list[str]] = [[]]
    length = 0
    for line in lines:
        if length + len(line) + 1 > 3900:
            chunks.append([])
            length = 0
        chunks[-1].append(line)
        length += len(line) + 1

    embed = discord.Embed(
        title=f"Cards with more than {threshold} copies ({len(cards)} found)",
        description="\n\n".join(chunks[0]),
        color=0xE67E22,
    )
    embed.set_footer(text="Select a card below to view entries and move copies.")
    return embed


class OvercountCardDetailView(discord.ui.View):
    """Shown after the user picks a card: entry multi-select + container select + move button."""

    def __init__(self, card: dict, containers: list[dict], threshold: int = 4):
        super().__init__(timeout=300)
        self._card = card
        self._threshold = threshold
        self._selected_entry_ids: list[int] = []
        self._selected_container_id: Optional[int] = None

        entries = card["entries"]

        # Entry multi-select (row 0)
        entry_options = []
        for e in entries[:25]:
            set_info = (e.get("set_code") or "?").upper()
            coll = e.get("collector_number") or ""
            if coll:
                set_info += f" #{coll}"
            cond = e.get("condition") or "NM"
            lang = (e.get("language") or "en").upper()
            foil = " ✨" if e.get("foil") else ""
            label = f"{set_info} · {cond} · {lang}{foil}"[:100]
            cont = e.get("container_name") or "no container"
            price = f"€{e['price_eur']:.2f}" if e.get("price_eur") else "no price"
            desc = f"📦 {cont}  ·  {price}"[:100]
            entry_options.append(
                discord.SelectOption(label=label, value=str(e["id"]), description=desc)
            )

        entry_sel = discord.ui.Select(
            placeholder="Select entries to move…",
            options=entry_options,
            min_values=1,
            max_values=min(25, len(entry_options)),
            row=0,
        )
        entry_sel.callback = self._on_entries
        self.add_item(entry_sel)

        # Container select (row 1)
        cont_options = [
            discord.SelectOption(
                label=c["name"][:100],
                value=str(c["id"]),
                description=(
                    f"{c.get('type','binder')} · {c['card_count']} cards"
                    + (f" · €{c['total_value_eur']:.2f}" if c.get("total_value_eur") else "")
                )[:100],
                emoji="📦",
            )
            for c in containers[:25]
        ]
        if cont_options:
            cont_sel = discord.ui.Select(
                placeholder="Move to container…",
                options=cont_options,
                row=1,
            )
            cont_sel.callback = self._on_container
            self.add_item(cont_sel)

        # Move button (row 2)
        move_btn = discord.ui.Button(
            label="Move selected", style=discord.ButtonStyle.primary, emoji="📦", row=2
        )
        move_btn.callback = self._on_move
        self.add_item(move_btn)

    async def _on_entries(self, interaction: discord.Interaction):
        self._selected_entry_ids = [int(v) for v in interaction.data["values"]]
        await interaction.response.defer()

    async def _on_container(self, interaction: discord.Interaction):
        self._selected_container_id = int(interaction.data["values"][0])
        await interaction.response.defer()

    async def _on_move(self, interaction: discord.Interaction):
        if not self._selected_entry_ids:
            await interaction.response.send_message(
                "Please select at least one entry to move.", ephemeral=True
            )
            return
        if self._selected_container_id is None:
            await interaction.response.send_message(
                "Please select a target container.", ephemeral=True
            )
            return
        n = await bot.db.move_cards_to_container(
            self._selected_entry_ids, self._selected_container_id
        )
        container = await bot.db.get_container(self._selected_container_id)
        cont_name = container["name"] if container else str(self._selected_container_id)
        await interaction.response.send_message(
            f"Moved **{n}** cop{'y' if n == 1 else 'ies'} to 📦 **{cont_name}**.",
            ephemeral=True,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class OvercountView(discord.ui.View):
    """Main overcount view: card-picker select + summary embed."""

    def __init__(self, cards: list[dict], containers: list[dict], threshold: int = 4):
        super().__init__(timeout=300)
        self._cards = cards
        self._containers = containers
        self._threshold = threshold
        self._card_map = {c["name_en"]: c for c in cards}

        options = []
        for card in cards[:25]:
            name = card.get("printed_name") or card.get("name_de") or card["name_en"]
            total = card["total"]
            excess = total - threshold
            prices = [e["price_eur"] for e in card["entries"] if e.get("price_eur")]
            price_hint = f"Σ €{sum(prices):.2f}  ·  " if prices else ""
            desc = f"{total}× total  ·  +{excess} excess  ·  {price_hint}select to manage"[:100]
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=card["name_en"],
                    description=desc,
                )
            )

        sel = discord.ui.Select(
            placeholder="Pick a card to manage…", options=options, row=0
        )
        sel.callback = self._on_card
        self.add_item(sel)

    async def _on_card(self, interaction: discord.Interaction):
        name_en = interaction.data["values"][0]
        card = self._card_map.get(name_en)
        if not card:
            await interaction.response.send_message("Card not found.", ephemeral=True)
            return

        entries = card["entries"]
        display_name = card.get("printed_name") or card.get("name_de") or name_en
        if display_name != name_en:
            display_name = f"{display_name} ({name_en})"

        embed = discord.Embed(
            title=f"Entries: {display_name}",
            description=f"**{card['total']}** copies total  ·  **{card['total'] - self._threshold}** excess",
            color=0xE67E22,
        )
        lines = []
        for e in entries:
            set_info = (e.get("set_code") or "?").upper()
            coll = e.get("collector_number") or ""
            if coll:
                set_info += f" #{coll}"
            cond = e.get("condition") or "NM"
            lang = (e.get("language") or "en").upper()
            foil = " ✨" if e.get("foil") else ""
            cont = e.get("container_name") or "_(no container)_"
            price = f"€{e['price_eur']:.2f}" if e.get("price_eur") else "—"
            lines.append(f"`#{e['id']}` {set_info} · **{cond}** · {lang}{foil} · 📦 {cont} · {price}")
        embed.add_field(name="Individual copies", value="\n".join(lines) or "—", inline=False)
        embed.set_footer(text="Select entries and a target container, then click Move.")

        view = OvercountCardDetailView(card, self._containers, self._threshold)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────────────
# /deck  — deckbuilder
# ──────────────────────────────────────────────────────────────────────────────

def _commander_embed(result: dict) -> discord.Embed:
    cmd = result["commander"]
    ci_str = "".join(sorted(deckbuilder.color_identity(cmd)))
    embed = discord.Embed(
        title=f"Commander Deck: {cmd.get('name_en', '?')}",
        description=f"Strategy: **{', '.join(result['themes']) or 'General'}**",
        color=0x8B0000,
    )
    if cmd.get("image_url"):
        embed.set_thumbnail(url=cmd["image_url"])
    embed.add_field(name="Colors", value=ci_str or "Colorless", inline=True)
    embed.add_field(name="From collection", value=f"{result['collection_count']}/99", inline=True)
    embed.add_field(name="Est. value", value=f"€{result['value_eur']:.2f}", inline=True)
    groups = result.get("groups", {})
    if groups:
        embed.add_field(
            name="Composition",
            value=" | ".join(f"{g}: {len(cs)}" for g, cs in sorted(groups.items())),
            inline=False,
        )
    if result["basics"]:
        embed.add_field(
            name="Basic Lands",
            value=", ".join(f"{n}× {land}" for land, n in sorted(result["basics"].items())),
            inline=False,
        )
    # Top 8 cards by value with container location
    all_cards = [c for cards in groups.values() for c in cards]
    all_cards.sort(key=lambda c: c.get("price_eur") or 0, reverse=True)
    if all_cards:
        lines = []
        for c in all_cards[:8]:
            container = c.get("container_name") or "—"
            price = f"  €{c['price_eur']:.2f}" if c.get("price_eur") else ""
            lines.append(f"• {c.get('name_en', '?')}  📦 {container}{price}")
        embed.add_field(name="Key cards", value="\n".join(lines), inline=False)
    embed.set_footer(text="Full deck list with containers attached as .txt")
    return embed


def _60_embed(result: dict) -> discord.Embed:
    fmt = result["format"].capitalize()
    embed = discord.Embed(
        title=f"{fmt} Deck Proposal",
        description=f"Strategy: **{result['strategy']}**",
        color=0x3498DB,
    )
    embed.add_field(name="From collection", value=f"{result['collection_count']}/36", inline=True)
    embed.add_field(name="Est. value", value=f"€{result['value_eur']:.2f}", inline=True)
    if result["basics"]:
        embed.add_field(
            name="Basic Lands",
            value=", ".join(f"{n}× {land}" for land, n in sorted(result["basics"].items())),
            inline=True,
        )
    top = result["deck"][:8]
    if top:
        lines = []
        for card, n in top:
            container = card.get("container_name") or "—"
            lines.append(f"• {n}× {card.get('name_en', '?')}  📦 {container}")
        embed.add_field(name="Key cards", value="\n".join(lines), inline=False)
    embed.set_footer(text="Full deck list attached as .txt")
    return embed


class SaveDeckModal(discord.ui.Modal, title="Save deck to container"):
    def __init__(self, card_ids: list[int], default_name: str):
        super().__init__()
        self._card_ids = card_ids
        self._name_input = discord.ui.TextInput(
            label="Container name",
            default=default_name[:100],
            max_length=100,
            placeholder="e.g. My Commander Deck",
        )
        self.add_item(self._name_input)

    async def on_submit(self, interaction: discord.Interaction):
        name = self._name_input.value.strip()
        container_id = await _get_or_create_container(name)
        n = await bot.db.move_cards_to_container(self._card_ids, container_id)
        await interaction.response.send_message(
            f"Moved **{n}** card(s) to 📦 **{name}**.", ephemeral=True
        )


class DeckResultView(discord.ui.View):
    """Shown after a deck proposal — lets the user accept, save, or decline it."""
    def __init__(self, card_ids: list[int] = None, deck_name: str = "Deck"):
        super().__init__(timeout=600)
        self._card_ids = card_ids or []
        self._deck_name = deck_name

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        self.clear_items()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Save to Container", style=discord.ButtonStyle.primary, emoji="📦")
    async def save_to_container(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._card_ids:
            await interaction.response.send_message("No collection cards to move.", ephemeral=True)
            return
        await interaction.response.send_modal(SaveDeckModal(self._card_ids, self._deck_name))

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        await interaction.delete_original_response()


class CommanderPickView(discord.ui.View):
    def __init__(self, pool: list[dict], commanders: list[tuple[dict, int]]):
        super().__init__(timeout=180)
        self._pool = pool
        self._commanders = commanders
        options = [
            discord.SelectOption(
                label=cmd["name_en"][:100],
                value=str(i),
                description=f"{''.join(sorted(deckbuilder.color_identity(cmd)))} | Synergy: {score}"[:100],
            )
            for i, (cmd, score) in enumerate(commanders)
        ]
        select = discord.ui.Select(placeholder="Choose your commander…", options=options)
        select.callback = self._pick
        self.add_item(select)

    async def _pick(self, interaction: discord.Interaction):
        idx = int(interaction.data["values"][0])
        commander, _ = self._commanders[idx]
        await interaction.response.defer()
        result = deckbuilder.build_commander_deck(commander, self._pool)
        embed = _commander_embed(result)
        decklist = deckbuilder.format_commander_decklist(result).encode("utf-8")
        cmd_name = commander.get("name_en") or "Commander"
        fname = cmd_name.replace(" ", "_").replace(",", "").lower()
        file = discord.File(io.BytesIO(decklist), filename=f"{fname}_deck.txt")
        card_ids = (
            [result["commander"]["id"]] if result["commander"].get("id") else []
        ) + [c["id"] for c in result["deck"] if c.get("id")] + [
            c["id"] for c in result.get("basics_from_collection", []) if c.get("id")
        ]
        await interaction.edit_original_response(
            embed=embed,
            view=DeckResultView(card_ids=card_ids, deck_name=cmd_name),
            attachments=[file],
        )


deck_group = app_commands.Group(name="deck", description="Build decks from your collection")


@deck_group.command(name="propose", description="Generate a deck proposal from your collection")
@app_commands.describe(format="Deck format")
@app_commands.choices(format=[
    app_commands.Choice(name="Commander", value="commander"),
    app_commands.Choice(name="Timeless (Arena)", value="timeless"),
    app_commands.Choice(name="Standard", value="standard"),
])
async def deck_propose(interaction: discord.Interaction, format: str = "commander"):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    pool = await bot.db.get_all()

    if not pool:
        await interaction.followup.send("Your collection is empty.", ephemeral=True)
        return

    if format == "commander":
        commanders = deckbuilder.rank_commanders(pool)
        if not commanders:
            await interaction.followup.send(
                "No commander-eligible cards found in your collection.\n"
                "Add some legendary creatures first!",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Choose a Commander",
            description="Select a commander from the dropdown to generate a deck proposal.",
            color=0x8B0000,
        )
        for cmd, score in commanders:
            ci = "".join(sorted(deckbuilder.color_identity(cmd)))
            embed.add_field(
                name=cmd["name_en"],
                value=f"{cmd.get('type_line', '?')} | {ci or 'Colorless'} | Synergy score: **{score}**",
                inline=False,
            )
        view = CommanderPickView(pool, commanders)
        await interaction.followup.send(embed=embed, view=view)

    else:
        result = deckbuilder.build_60_deck(pool, format)
        if not result["deck"]:
            await interaction.followup.send(
                f"Not enough {format.capitalize()}-legal cards in your collection.",
                ephemeral=True,
            )
            return
        embed = _60_embed(result)
        decklist = deckbuilder.format_60_decklist(result).encode("utf-8")
        file = discord.File(io.BytesIO(decklist), filename=f"{format}_deck.txt")
        card_ids = [c["id"] for c, _ in result["deck"] if c.get("id")] + [
            c["id"] for c in result.get("basics_from_collection", []) if c.get("id")
        ]
        deck_name = f"{result['strategy']} {format.capitalize()}"
        await interaction.followup.send(
            embed=embed, file=file,
            view=DeckResultView(card_ids=card_ids, deck_name=deck_name),
        )


bot.tree.add_command(deck_group)


# ──────────────────────────────────────────────────────────────────────────────
# /showcase  —  top 5 most valuable cards with price history + navigation
# ──────────────────────────────────────────────────────────────────────────────

_SHOWCASE_RARITY_COLOUR = {
    "mythic":   0xe8742a,
    "rare":     0xc3a343,
    "uncommon": 0x6e7f8d,
    "common":   0x393939,
}


_chart_fail_count: dict[str, int] = {}


def _make_price_chart(history: list[dict], card_name: str) -> Optional[bytes]:
    """Render a price-history line chart as PNG bytes. Returns None if < 2 points."""
    if len(history) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as _dt

        dates  = [_dt.strptime(h["recorded_at"], "%Y-%m-%d") for h in history]
        prices = [h["price_eur"] for h in history]

        fig, ax = plt.subplots(figsize=(6, 2.4), facecolor="#2b2d31")
        ax.set_facecolor("#383a40")
        ax.plot(dates, prices, color="#5865f2", linewidth=2, marker="o", markersize=4)
        ax.fill_between(dates, prices, alpha=0.15, color="#5865f2")
        ax.set_ylabel("EUR", color="#dbdee1", fontsize=8)
        ax.tick_params(colors="#dbdee1", labelsize=7)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        for spine in ax.spines.values():
            spine.set_color("#4e5058")
        fig.autofmt_xdate(rotation=30, ha="right")
        fig.tight_layout(pad=0.5)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        count = _chart_fail_count.get(card_name, 0) + 1
        _chart_fail_count[card_name] = count
        log_fn = logger.error if count >= 3 else logger.warning
        log_fn("Price chart failed for %r (attempt %d): %s", card_name, count, e)
        return None


def _showcase_embed(card: dict, rank: int, total: int) -> discord.Embed:
    name_en     = card.get("name_en") or "Unknown"
    loc_name    = card.get("printed_name") or card.get("name_de") or name_en
    display     = loc_name if loc_name != name_en else name_en
    title_name  = display if display == name_en else f"{display} ({name_en})"
    foil_tag    = " ✨" if card.get("foil") else ""
    rarity      = (card.get("rarity") or "").lower()
    colour      = _SHOWCASE_RARITY_COLOUR.get(rarity, 0x5865f2)

    price_str   = f"**{_fmt_price(card)}**"

    set_code    = (card.get("set_code") or "").upper()
    set_name    = card.get("set_name") or ""
    coll_nr     = card.get("collector_number") or ""
    type_line   = card.get("type_line") or "—"
    condition   = card.get("condition") or "NM"
    language    = (card.get("language") or "en").upper()
    container   = card.get("container_name") or "—"
    mana_cost   = card.get("mana_cost") or ""
    cmc         = card.get("cmc")
    power       = card.get("power")
    toughness   = card.get("toughness")
    loyalty     = card.get("loyalty")
    oracle_text = card.get("oracle_text") or ""
    flavor_text = card.get("flavor_text") or ""
    keywords    = card.get("keywords") or []
    colors      = card.get("colors") or []

    embed = discord.Embed(
        title=f"#{rank}/{total} — {title_name}{foil_tag}",
        colour=colour,
    )

    # Row 1
    embed.add_field(name="Price", value=price_str, inline=True)
    embed.add_field(name="Rarity", value=rarity.capitalize() or "—", inline=True)
    embed.add_field(name="Container", value=f"📦 {container}", inline=True)
    # Row 2
    embed.add_field(name="Set", value=f"{set_name} ({set_code}) #{coll_nr}", inline=True)
    embed.add_field(name="Type", value=type_line, inline=True)
    embed.add_field(name="Condition", value=f"{condition} · {language}", inline=True)
    # Row 3: mana / stats / keywords
    if mana_cost:
        mana_str = mana_cost
        if cmc is not None:
            cv = int(cmc) if cmc == int(cmc) else cmc
            mana_str += f"  (CMC {cv})"
        embed.add_field(name="Mana Cost", value=mana_str, inline=True)
    if power is not None and toughness is not None:
        embed.add_field(name="P / T", value=f"{power} / {toughness}", inline=True)
    elif loyalty is not None:
        embed.add_field(name="Loyalty", value=str(loyalty), inline=True)
    if colors:
        color_map = {"W": "⚪", "U": "🔵", "B": "⚫", "R": "🔴", "G": "🟢", "C": "◇"}
        embed.add_field(
            name="Colors",
            value="".join(color_map.get(c, c) for c in colors) or "Colorless",
            inline=True,
        )
    if keywords and isinstance(keywords, list):
        embed.add_field(name="Keywords", value=", ".join(keywords), inline=False)
    if oracle_text:
        snippet = oracle_text[:512] + ("…" if len(oracle_text) > 512 else "")
        embed.add_field(name="Oracle Text", value=snippet, inline=False)
    if flavor_text:
        snippet = flavor_text[:256] + ("…" if len(flavor_text) > 256 else "")
        embed.add_field(name="​", value=f"*{snippet}*", inline=False)

    if card.get("image_url"):
        embed.set_thumbnail(url=card["image_url"])
    embed.set_footer(text=f"Collection ID #{card.get('id')}  ·  Card {rank} of {total}")
    return embed


async def _load_showcase_data(limit: int = 5) -> tuple[list[dict], list[list[dict]], list[Optional[bytes]]]:
    """Fetch top cards, their price histories, and pre-render charts."""
    cards = await bot.db.get_top_by_value(limit)
    histories: list[list[dict]] = []
    charts: list[Optional[bytes]] = []
    for card in cards:
        history: list[dict] = []
        if card.get("scryfall_id"):
            history = await bot.db.get_price_history(card["scryfall_id"])
        histories.append(history)
        chart = await asyncio.to_thread(
            _make_price_chart, history, card.get("name_en", "")
        )
        charts.append(chart)
    return cards, histories, charts


def _showcase_send_kwargs(
    card: dict, rank: int, total: int,
    history: list[dict], chart: Optional[bytes],
) -> tuple[discord.Embed, Optional[discord.File]]:
    """Build (embed, file_or_None) for a showcase card."""
    embed = _showcase_embed(card, rank, total)
    if chart:
        fname = f"chart_{rank}.png"
        embed.set_image(url=f"attachment://{fname}")
        return embed, discord.File(io.BytesIO(chart), filename=fname)
    # No chart — add text history note
    if len(history) == 1:
        hist_text = f"First recorded: {history[0]['recorded_at']} — €{history[0]['price_eur']:.2f}"
    elif history:
        hist_text = f"{len(history)} data points — latest €{history[-1]['price_eur']:.2f}"
    else:
        hist_text = "No history yet — prices are recorded automatically once a day."
    embed.add_field(name="Price History", value=hist_text, inline=False)
    return embed, None


class ShowcaseView(discord.ui.View):
    """Paginated navigation through the top-N showcase cards."""

    def __init__(
        self,
        cards: list[dict],
        histories: list[list[dict]],
        charts: list[Optional[bytes]],
        index: int = 0,
        target_user_id: Optional[int] = None,
    ):
        super().__init__(timeout=300)
        self._cards = cards
        self._histories = histories
        self._charts = charts
        self._index = index
        self._target_user_id = target_user_id
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        n = len(self._cards)
        prev = discord.ui.Button(
            label="◀ Prev", style=discord.ButtonStyle.secondary,
            disabled=self._index == 0, row=0,
        )
        prev.callback = self._prev
        self.add_item(prev)

        counter = discord.ui.Button(
            label=f"{self._index + 1} / {n}",
            style=discord.ButtonStyle.secondary, disabled=True, row=0,
        )
        self.add_item(counter)

        nxt = discord.ui.Button(
            label="Next ▶", style=discord.ButtonStyle.secondary,
            disabled=self._index >= n - 1, row=0,
        )
        nxt.callback = self._next
        self.add_item(nxt)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if self._target_user_id and interaction.user.id != self._target_user_id:
            await interaction.response.send_message(
                "This showcase was opened for another user.", ephemeral=True
            )
            return False
        return True

    async def _go(self, interaction: discord.Interaction, idx: int):
        self._index = idx
        self._rebuild()
        card = self._cards[idx]
        embed, file = _showcase_send_kwargs(
            card, idx + 1, len(self._cards), self._histories[idx], self._charts[idx]
        )
        await interaction.response.edit_message(
            embed=embed, view=self,
            attachments=[file] if file else [],
        )

    async def _prev(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await self._go(interaction, self._index - 1)

    async def _next(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await self._go(interaction, self._index + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class WelcomeView(discord.ui.View):
    """Welcome menu posted when a new member joins the showcase channel."""

    def __init__(self):
        super().__init__(timeout=86400)  # 24 h

    @discord.ui.button(label="🃏 Showcase", style=discord.ButtonStyle.primary, row=0)
    async def btn_showcase(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cards, histories, charts = await _load_showcase_data(5)
        except Exception as e:
            logger.warning("Welcome showcase failed: %s", e)
            await interaction.followup.send("Failed to load showcase.", ephemeral=True)
            return
        if not cards:
            await interaction.followup.send("No priced cards in the collection yet.", ephemeral=True)
            return
        embed, file = _showcase_send_kwargs(cards[0], 1, len(cards), histories[0], charts[0])
        view = ShowcaseView(cards, histories, charts, index=0, target_user_id=interaction.user.id)
        if file:
            await interaction.followup.send(embed=embed, view=view, file=file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="📦 Browse", style=discord.ButtonStyle.secondary, row=0)
    async def btn_browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        containers = await bot.db.list_containers()
        if not containers:
            await interaction.response.send_message("No containers yet.", ephemeral=True)
            return
        lines = []
        for c in containers:
            val = f" · €{c['total_value_eur']:.2f}" if c.get("total_value_eur") else ""
            lines.append(f"📦 **{c['name']}** — {c['card_count']} cards{val}")
        embed = discord.Embed(
            title="Collection — Containers",
            description="\n".join(lines),
            color=0x5865f2,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📊 Stats", style=discord.ButtonStyle.secondary, row=0)
    async def btn_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = await bot.db.stats()
        if not s:
            await interaction.response.send_message("No stats available yet.", ephemeral=True)
            return
        embed = discord.Embed(title="Collection Stats", color=0x5865f2)
        embed.add_field(name="Total cards", value=str(s.get("total_cards", 0)), inline=True)
        embed.add_field(name="Unique cards", value=str(s.get("unique_cards", 0)), inline=True)
        embed.add_field(name="Total value", value=f"€{s.get('total_value_eur', 0):.2f}", inline=True)
        embed.add_field(name="Foil cards", value=str(s.get("foil_total", 0)), inline=True)
        r_line = "  ·  ".join([
            f"{s.get('r_mythic', 0)} Mythic",
            f"{s.get('r_rare', 0)} Rare",
            f"{s.get('r_uncommon', 0)} Uncommon",
            f"{s.get('r_common', 0)} Common",
        ])
        embed.add_field(name="By rarity", value=r_line, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="ℹ️ Commands", style=discord.ButtonStyle.secondary, row=0)
    async def btn_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Available Commands", color=0x5865f2, description=(
            "**Adding cards**\n"
            "`/add` — Add a card by name (EN/DE auto-detected)\n"
            "Drop an image in the scan channel — scans automatically\n\n"
            "**Viewing**\n"
            "`/list` — Browse full collection (paginated)\n"
            "`/search` — Full-text search across all fields\n"
            "`/browse` — Browse containers and manage cards interactively\n"
            "`/stats` — Collection stats + overcounted cards button\n"
            "`/showcase` — Top 5 most valuable cards\n\n"
            "**Containers**\n"
            "`/container list` — All containers with card count & value\n"
            "`/container move` — Move all cards from one container to another\n"
            "Rename/delete containers via Browse → select container\n\n"
            "**Other**\n"
            "`/export` — Download as Moxfield CSV / JSON\n"
            "`/deck propose` — Build a deck from your collection\n"
            "`/backup create` — Download the database (admin)"
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)




@bot.tree.command(name="showcase", description="Show the 5 most valuable cards in your collection")
async def cmd_showcase(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        cards, histories, charts = await _load_showcase_data(5)
    except Exception as e:
        logger.warning("Showcase load failed: %s", e)
        await interaction.followup.send("Failed to load showcase data.", ephemeral=True)
        return
    if not cards:
        await interaction.followup.send(
            "No cards with a known price in your collection yet.", ephemeral=True
        )
        return
    embed, file = _showcase_send_kwargs(cards[0], 1, len(cards), histories[0], charts[0])
    view = ShowcaseView(cards, histories, charts, index=0, target_user_id=interaction.user.id)
    if file:
        await interaction.followup.send(embed=embed, view=view, file=file, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /backup — create & restore  (admin only)
# ──────────────────────────────────────────────────────────────────────────────

backup_group = app_commands.Group(name="backup", description="Database backup and restore (admin only)")


@backup_group.command(name="create", description="Download the current database as a backup file")
async def backup_create(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    await interaction.edit_original_response(content="Creating backup, please wait...")
    try:
        data = await bot.db.backup_bytes()
    except Exception as exc:
        logger.exception("Backup failed")
        await interaction.edit_original_response(content=f"Backup failed: {exc}")
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_filename = f"mtg_collection_{ts}.db"

    # Save uncompressed copy to server
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    local_path = BACKUP_DIR / base_filename
    await asyncio.to_thread(local_path.write_bytes, data)

    # Compress for Discord upload
    await interaction.edit_original_response(content="Compressing for upload...")
    gz_data = await asyncio.to_thread(gzip.compress, data, compresslevel=6)
    gz_filename = base_filename + ".gz"
    size_raw_mb = len(data) / 1024 / 1024
    size_gz_mb = len(gz_data) / 1024 / 1024

    await interaction.edit_original_response(
        content=f"Backup saved on server: `{local_path}` ({size_raw_mb:.1f} MB)"
    )
    await interaction.followup.send(
        content=f"Compressed backup for download — `{gz_filename}` ({size_gz_mb:.2f} MB).",
        file=discord.File(io.BytesIO(gz_data), filename=gz_filename),
        ephemeral=True,
    )


@backup_group.command(name="restore", description="Restore the database from a backup file")
@app_commands.describe(file="A .db backup file previously created with /backup create")
async def backup_restore(interaction: discord.Interaction, file: discord.Attachment):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not (file.filename.endswith(".db") or file.filename.endswith(".db.gz")):
        await interaction.followup.send("Please attach a `.db` or `.db.gz` backup file.", ephemeral=True)
        return
    await interaction.edit_original_response(content="Reading backup file...")
    raw = await file.read()
    if file.filename.endswith(".gz"):
        await interaction.edit_original_response(content="Decompressing backup...")
        data = await asyncio.to_thread(gzip.decompress, raw)
    else:
        data = raw
    await interaction.edit_original_response(content="Validating backup...")
    try:
        counts = await Database.inspect_backup(data)
    except ValueError as exc:
        await interaction.followup.send(f"Invalid backup: {exc}", ephemeral=True)
        return
    except Exception as exc:
        logger.exception("Could not inspect backup")
        await interaction.followup.send(f"Could not read backup file: {exc}", ephemeral=True)
        return
    embed = discord.Embed(
        title="Restore database?",
        description=(
            f"**Backup contains:** {counts['cards']} cards · {counts['containers']} containers\n\n"
            "This will **replace the current database** with the backup.\n"
            "All changes made after the backup was created will be lost."
        ),
        color=0xFF4444,
    )
    view = RestoreConfirmView(data)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


bot.tree.add_command(backup_group)


class RestoreConfirmView(discord.ui.View):
    def __init__(self, data: bytes):
        super().__init__(timeout=60)
        self._data = data

    @discord.ui.button(label="Yes, restore", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()
        if bot.db._restore_lock.locked():
            await interaction.edit_original_response(
                content="A restore is already in progress. Please wait.", embed=None, view=None
            )
            return
        await interaction.edit_original_response(
            content="Restoring database, please wait...", embed=None, view=None
        )
        try:
            await bot.db.restore_from_bytes(self._data)
        except Exception as exc:
            logger.exception("Restore failed")
            await interaction.edit_original_response(
                content=f"Restore failed: {exc}", embed=None, view=None
            )
            return
        await interaction.edit_original_response(
            content="Database restored successfully.", embed=None, view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Restore cancelled.", embed=None, view=None)


# ──────────────────────────────────────────────────────────────────────────────
# Auto-scan channel  (drop a photo → bot processes it instantly)
# ──────────────────────────────────────────────────────────────────────────────

# Per-user last-used container: user_id -> (container_id, container_name)
_last_container: dict[int, tuple[int, str]] = {}

# Filename extensions accepted as images when Discord omits content_type
_SCAN_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "heic"}


def _no_match_msg(m: "_ScanMatch") -> str:
    """Human-readable reason why a scan produced no card match."""
    ci = m.collector_info
    if not scanner.ocr_available():
        return "OCR not available. Use `/add <name>` instead."
    if ci.get("set_code") and ci.get("collector_number"):
        return (
            f'Collector info read ({ci["set_code"]} #{ci["collector_number"]}) '
            f"but no Scryfall match. Enter the name manually."
        )
    if m.extracted_name:
        return f'Could not match **"{m.extracted_name}"** on Scryfall. Enter the name manually.'
    return "Could not read the card. Enter the name manually."


async def _do_scan_and_confirm(
    interaction: discord.Interaction,
    image_bytes: bytes,
    source_message: discord.Message,
    container_id: int,
    container_name: str,
):
    """OCR → Scryfall → show confirmation. Caller must not have responded yet (interaction-based)."""
    await interaction.response.defer(thinking=True)

    if DEBUG_SCAN_PREVIEW:
        preview = scanner.get_isolated_preview(image_bytes)
        if preview:
            await interaction.followup.send(
                "🔍 **Debug preview** — isolated card + OCR name zone (red box):",
                file=discord.File(io.BytesIO(preview), filename="debug_preview.jpg"),
                ephemeral=True,
            )

    m = await _resolve_scan(image_bytes)
    logger.debug("OCR name: %r  footer: %s", m.extracted_name, m.collector_info)

    if DEBUG_SCAN_PREVIEW:
        ci = m.collector_info
        dbg = []
        if m.extracted_name:
            dbg.append(f"**OCR Name:** `{m.extracted_name}`")
        dbg.append(
            f"**Footer:** set=`{ci.get('set_code') or '—'}` "
            f"#=`{ci.get('collector_number') or '—'}` "
            f"lang=`{ci.get('language') or '—'}`"
        )
        await interaction.followup.send("🔍 **Debug — OCR results:**\n" + "\n".join(dbg), ephemeral=True)

    if not m.card:
        view = _ManualNameView(image_bytes, source_message, container_id, container_name)
        await interaction.followup.send(_no_match_msg(m), view=view, ephemeral=True)
        return

    card = m.card
    card["language"] = m.detected_lang or "en"
    card["container_id"] = container_id
    card["container_name"] = container_name
    match_method = "  •  ".join(m.method_parts)
    lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
    embed = card_embed(card, title_prefix="Found — confirm?  ")
    embed.description = (
        f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
        + (f"\n*{match_method}*" if match_method else "")
    )
    containers = await bot.db.list_containers()
    view = ScanConfirmView(card, source_message, image_bytes, containers, match_method)
    await interaction.followup.send(embed=embed, view=view)


async def _do_scan_direct(
    scanning_msg: discord.Message,
    image_bytes: bytes,
    source_message: discord.Message,
    container_id: int,
    container_name: str,
):
    """OCR → Scryfall → edit scanning_msg with confirmation (no interaction — used when container already known)."""
    try:
        m = await _resolve_scan(image_bytes)
        logger.debug("OCR name: %r  footer: %s", m.extracted_name, m.collector_info)

        if not m.card:
            view = _ManualNameView(image_bytes, source_message, container_id, container_name)
            await scanning_msg.edit(content=_no_match_msg(m), view=view)
            return

        card = m.card
        card["language"] = m.detected_lang or "en"
        card["container_id"] = container_id
        card["container_name"] = container_name
        match_method = "  •  ".join(m.method_parts)
        lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
        embed = card_embed(card, title_prefix="Found — confirm?  ")
        embed.description = (
            f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
            + (f"\n*{match_method}*" if match_method else "")
        )
        containers = await bot.db.list_containers()
        view = ScanConfirmView(card, source_message, image_bytes, containers, match_method)
        await scanning_msg.edit(content=None, embed=embed, view=view)
    except Exception as exc:
        logger.error("_do_scan_direct failed: %s", exc, exc_info=True)
        try:
            await scanning_msg.edit(content="⚠️ Scan failed — try again or use `/add`.", embed=None, view=None)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# /browse — interactive container & card browser
# ──────────────────────────────────────────────────────────────────────────────

_BROWSE_PAGE_SIZE = 25


def _card_manage_embed(card: dict) -> discord.Embed:
    printed = card.get("printed_name") or card.get("name_en") or "Unknown"
    name_en = card.get("name_en") or ""
    title = printed if printed == name_en else f"{printed} ({name_en})"
    embed = discord.Embed(title=title, color=0xE74C3C)
    if card.get("image_url"):
        embed.set_thumbnail(url=card["image_url"])
    embed.add_field(name="Set", value=_fmt_set(card), inline=True)
    embed.add_field(name="Type", value=card.get("type_line") or "—", inline=True)
    embed.add_field(name="Condition", value=_fmt_condition(card), inline=True)
    embed.add_field(name="Price (EUR)", value=_fmt_price(card), inline=True)
    embed.add_field(name="Container", value=f"📦 {card.get('container_name') or '—'}", inline=True)
    if card.get("notes"):
        embed.add_field(name="Notes", value=card["notes"], inline=False)
    embed.set_footer(text=f"Collection ID: {card['id']}")
    return embed


class ContainerCreateModal(discord.ui.Modal, title="Create container"):
    cont_name = discord.ui.TextInput(label="Container name", placeholder="e.g. Binder 1", max_length=100)
    cont_type = discord.ui.TextInput(
        label="Type  (binder / box / deck / trade / other)",
        placeholder="binder",
        required=False,
        max_length=20,
    )
    cont_desc = discord.ui.TextInput(
        label="Description (optional)",
        placeholder="e.g. Blue cards from 2023",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, refresh_browse: bool = True):
        super().__init__()
        self._refresh_browse = refresh_browse

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_collector(interaction):
            return
        type_val = (self.cont_type.value or "binder").strip().lower()
        if type_val not in CONTAINER_TYPES:
            type_val = "binder"
        name = self.cont_name.value.strip()
        desc = self.cont_desc.value.strip() or ""
        try:
            await bot.db.create_container(name, desc, type_val)
        except Exception:
            await interaction.response.send_message(
                f'A container named **{name}** already exists.', ephemeral=True
            )
            return
        if self._refresh_browse:
            containers = await bot.db.list_containers()
            view = BrowseContainersView(containers)
            await interaction.response.edit_message(
                content="Select a container to browse:", embed=None, view=view
            )
        else:
            await interaction.response.send_message(
                f'📦 Container **{name}** (`{type_val}`) created.', ephemeral=True
            )


class ContainerRenameModal(discord.ui.Modal, title="Rename container"):
    new_name = discord.ui.TextInput(label="New name", max_length=100)

    def __init__(self, container: dict, page: int, total: int):
        super().__init__()
        self._container = container
        self._page = page
        self._total = total
        self.new_name.default = container["name"]

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        name = self.new_name.value.strip()
        ok = await bot.db.rename_container(self._container["id"], name)
        if not ok:
            await interaction.response.send_message("Could not rename container.", ephemeral=True)
            return
        self._container["name"] = name
        cards = await bot.db.list_cards(
            limit=_BROWSE_PAGE_SIZE, offset=self._page * _BROWSE_PAGE_SIZE,
            container_id=self._container["id"],
        )
        view = BrowseCardsView(self._container, cards, self._total, self._page)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)


class _BrowseContainerDeleteConfirmView(discord.ui.View):
    def __init__(self, container: dict):
        super().__init__(timeout=30)
        self._container = container

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if not await require_admin(interaction):
            return
        await bot.db.delete_container(self._container["id"])
        containers = await bot.db.list_containers()
        view = BrowseContainersView(containers)
        await interaction.response.edit_message(
            content=f'Container **{self._container["name"]}** deleted. Cards were kept.',
            embed=None, view=view,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        cards = await bot.db.list_cards(
            limit=_BROWSE_PAGE_SIZE, offset=0, container_id=self._container["id"]
        )
        total = await bot.db.count_cards(container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, total, page=0)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)


class BrowseContainersView(discord.ui.View):
    def __init__(self, containers: list[dict]):
        super().__init__(timeout=300)
        if containers:
            options = [
                discord.SelectOption(
                    label=c["name"][:100],
                    value=str(c["id"]),
                    description=(
                        f"{c.get('type', 'binder')} · {c['card_count']} cards"
                        + (f" · €{c['total_value_eur']:.2f}" if c.get("total_value_eur") else "")
                    )[:100],
                    emoji="📦",
                )
                for c in containers[:25]
            ]
            sel = discord.ui.Select(placeholder="Select a container…", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        create_btn = discord.ui.Button(
            label="New Container", emoji="➕", style=discord.ButtonStyle.primary, row=1
        )
        create_btn.callback = self._create
        self.add_item(create_btn)

    async def _on_select(self, interaction: discord.Interaction):
        container_id = int(interaction.data["values"][0])
        container = await bot.db.get_container(container_id)
        if not container:
            await interaction.response.edit_message(content="Container no longer exists.", embed=None, view=None)
            return
        total = await bot.db.count_cards(container_id=container_id)
        cards = await bot.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=0, container_id=container_id)
        view = BrowseCardsView(container, cards, total, page=0)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)

    async def _create(self, interaction: discord.Interaction):
        if not await require_collector(interaction):
            return
        await interaction.response.send_modal(ContainerCreateModal(refresh_browse=True))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class BrowseCardsView(discord.ui.View):
    def __init__(self, container: dict, cards: list[dict], total: int, page: int):
        super().__init__(timeout=300)
        self._container = container
        self._total = total
        self._page = page
        self._pages = max(1, (total + _BROWSE_PAGE_SIZE - 1) // _BROWSE_PAGE_SIZE)

        if cards:
            options = [
                discord.SelectOption(
                    label=self._label(c),
                    value=str(c["id"]),
                    description=self._desc(c),
                )
                for c in cards
            ]
            sel = discord.ui.Select(
                placeholder=f"Select a card… ({total} in container)",
                options=options,
                row=0,
            )
            sel.callback = self._on_card
            self.add_item(sel)
        else:
            empty = discord.ui.Button(label="(empty container)", style=discord.ButtonStyle.secondary, disabled=True, row=0)
            self.add_item(empty)

        back_btn = discord.ui.Button(label="◀ Containers", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back
        self.add_item(back_btn)

        if page > 0:
            prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)

        if (page + 1) * _BROWSE_PAGE_SIZE < total:
            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

        rename_btn = discord.ui.Button(label="Rename", emoji="✏️", style=discord.ButtonStyle.secondary, row=2)
        rename_btn.callback = self._rename
        self.add_item(rename_btn)

        delete_btn = discord.ui.Button(label="Delete Container", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
        delete_btn.callback = self._delete
        self.add_item(delete_btn)

    @staticmethod
    def _label(c: dict) -> str:
        return _card_select_label(c)

    @staticmethod
    def _desc(c: dict) -> str:
        parts = [(c.get("set_code") or "?").upper(), c.get("condition", "NM")]
        if c.get("price_eur"):
            parts.append(f"€{c['price_eur']:.2f}")
        return "  ·  ".join(parts)[:100]

    def make_embed(self) -> discord.Embed:
        c = self._container
        embed = discord.Embed(
            title=f"📦 {c['name']}",
            description=f"{c.get('type', 'binder').capitalize()} · {self._total} card(s)",
            color=0x5865F2,
        )
        if self._pages > 1:
            embed.set_footer(text=f"Page {self._page + 1} / {self._pages}")
        return embed

    async def _on_card(self, interaction: discord.Interaction):
        card_id = int(interaction.data["values"][0])
        card = await bot.db.get_card(card_id)
        if not card:
            await interaction.response.edit_message(content="Card not found.", embed=None, view=None)
            return
        view = CardManageView(card, self._container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(card), view=view)

    async def _back(self, interaction: discord.Interaction):
        containers = await bot.db.list_containers()
        view = BrowseContainersView(containers)
        await interaction.response.edit_message(content="Select a container to browse:", embed=None, view=view)

    async def _prev(self, interaction: discord.Interaction):
        page = self._page - 1
        cards = await bot.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, self._total, page)
        await interaction.response.edit_message(embed=view.make_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        page = self._page + 1
        cards = await bot.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, self._total, page)
        await interaction.response.edit_message(embed=view.make_embed(), view=view)

    async def _rename(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.send_modal(
            ContainerRenameModal(self._container, self._page, self._total)
        )

    async def _delete(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        name = self._container["name"]
        view = _BrowseContainerDeleteConfirmView(self._container)
        await interaction.response.edit_message(
            content=f'Delete container **{name}**? Cards in it will not be deleted.',
            embed=None, view=view,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class CardManageView(discord.ui.View):
    def __init__(self, card: dict, container: Optional[dict], page: int):
        super().__init__(timeout=300)
        self._card = card
        self._container = container
        self._page = page
        if container is None:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.label == "◀ Back":
                    item.label = "✕ Close"
                    break

    @discord.ui.button(label="Move", style=discord.ButtonStyle.primary, emoji="📦", row=0)
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        containers = await bot.db.list_containers()
        others = [c for c in containers if c["id"] != self._card.get("container_id")]
        if not others:
            await interaction.response.send_message("No other containers available.", ephemeral=True)
            return
        view = MoveCardView(self._card, others, self._container, self._page)
        await interaction.response.edit_message(content="Select destination container:", embed=None, view=view)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        await interaction.response.send_modal(EditCardModal(self._card, self._container, self._page))

    @discord.ui.button(label="Resync", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def resync(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        scryfall_id = self._card.get("scryfall_id")
        if not scryfall_id:
            await interaction.response.send_message("Card has no Scryfall ID.", ephemeral=True)
            return
        await interaction.response.defer()
        fresh = await bot.scryfall.get_by_id(scryfall_id)
        if not fresh:
            await interaction.followup.send("Scryfall returned no data for this card.", ephemeral=True)
            return
        await bot.db.resync_card(scryfall_id, fresh)
        card = await bot.db.get_card(self._card["id"])
        self._card = card
        await interaction.edit_original_response(embed=_card_manage_embed(card), view=self)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_admin(interaction):
            return
        view = _BrowseDeleteConfirmView(self._card, self._container, self._page)
        name = self._card.get("name_en") or "this card"
        await interaction.response.edit_message(
            content=f"Delete **{name}** (ID {self._card['id']}) from the collection?",
            embed=None, view=view,
        )

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._container is None:
            await interaction.response.edit_message(content="✕", embed=None, view=None)
            return
        total = await bot.db.count_cards(container_id=self._container["id"])
        page = min(self._page, max(0, (total - 1) // _BROWSE_PAGE_SIZE)) if total else 0
        cards = await bot.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, total, page)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)


class MoveCardView(discord.ui.View):
    def __init__(self, card: dict, containers: list[dict], current_container: Optional[dict], page: int):
        super().__init__(timeout=300)
        self._card = card
        self._current_container = current_container
        self._page = page
        options = [
            discord.SelectOption(
                label=c["name"][:100],
                value=str(c["id"]),
                description=f"{c.get('type', 'binder')} · {c['card_count']} cards"[:100],
                emoji="📦",
            )
            for c in containers[:25]
        ]
        sel = discord.ui.Select(placeholder="Move to…", options=options, row=0)
        sel.callback = self._on_select
        self.add_item(sel)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _on_select(self, interaction: discord.Interaction):
        new_id = int(interaction.data["values"][0])
        await bot.db.update_card(self._card["id"], "container_id", new_id)
        card = await bot.db.get_card(self._card["id"])
        view = CardManageView(card, self._current_container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(card), view=view)

    async def _cancel(self, interaction: discord.Interaction):
        view = CardManageView(self._card, self._current_container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(self._card), view=view)


class _BrowseDeleteConfirmView(discord.ui.View):
    def __init__(self, card: dict, container: Optional[dict], page: int):
        super().__init__(timeout=60)
        self._card = card
        self._container = container
        self._page = page

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        name = self._card.get("name_en") or "Card"
        await bot.db.remove_card(self._card["id"])
        if self._container is None:
            await interaction.response.edit_message(content=f"**{name}** removed.", embed=None, view=None)
            return
        total = await bot.db.count_cards(container_id=self._container["id"])
        page = min(self._page, max(0, (total - 1) // _BROWSE_PAGE_SIZE)) if total else 0
        cards = await bot.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, total, page)
        await interaction.response.edit_message(content=f"Deleted **{name}**.", embed=view.make_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = CardManageView(self._card, self._container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(self._card), view=view)


class EditCardModal(discord.ui.Modal, title="Edit card"):
    def __init__(self, card: dict, container: Optional[dict], page: int):
        super().__init__()
        self._card = card
        self._container = container
        self._page = page
        self._condition_input = discord.ui.TextInput(
            label="Condition",
            placeholder="NM / LP / MP / HP / DMG",
            default=card.get("condition") or "NM",
            required=False,
            max_length=3,
        )
        self._language_input = discord.ui.TextInput(
            label="Language",
            placeholder="en / de",
            default=card.get("language") or "en",
            required=False,
            max_length=5,
        )
        self._foil_input = discord.ui.TextInput(
            label="Foil (0 = no, 1 = yes)",
            placeholder="0 or 1",
            default="1" if card.get("foil") else "0",
            required=False,
            max_length=1,
        )
        self._notes_input = discord.ui.TextInput(
            label="Notes",
            placeholder="Free-text notes…",
            default=card.get("notes") or "",
            required=False,
            max_length=200,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self._condition_input)
        self.add_item(self._language_input)
        self.add_item(self._foil_input)
        self.add_item(self._notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        card_id = self._card["id"]
        cond = self._condition_input.value.strip().upper()
        if cond in ("NM", "LP", "MP", "HP", "DMG"):
            await bot.db.update_card(card_id, "condition", cond)
        lang = self._language_input.value.strip().lower()
        if lang in ("en", "de"):
            await bot.db.update_card(card_id, "language", lang)
        foil_val = self._foil_input.value.strip()
        if foil_val in ("0", "1"):
            await bot.db.update_card(card_id, "foil", int(foil_val))
        notes = self._notes_input.value.strip() or None
        await bot.db.update_card(card_id, "notes", notes)
        card = await bot.db.get_card(card_id)
        view = CardManageView(card, self._container, self._page)
        await interaction.response.edit_message(embed=_card_manage_embed(card), view=view)


class NewContainerModal(discord.ui.Modal, title="New container"):
    cont_name = discord.ui.TextInput(label="Container name", placeholder="e.g. Binder 1", max_length=100)
    cont_type = discord.ui.TextInput(
        label="Type  (binder / box / deck / trade / other)",
        placeholder="binder",
        required=False,
        max_length=20,
    )

    def __init__(self, image_bytes: bytes, source_message: discord.Message):
        super().__init__()
        self._image_bytes = image_bytes
        self._source = source_message

    async def on_submit(self, interaction: discord.Interaction):
        type_val = (self.cont_type.value or "binder").strip().lower()
        if type_val not in CONTAINER_TYPES:
            type_val = "binder"
        name = self.cont_name.value.strip()
        try:
            cid = await bot.db.create_container(name, type=type_val)
        except Exception:
            containers = await bot.db.list_containers()
            existing = next((c for c in containers if c["name"] == name), None)
            if not existing:
                await interaction.response.send_message("Could not create container.", ephemeral=True)
                return
            cid = existing["id"]
        _last_container[self._source.author.id] = (cid, name)
        await _do_scan_and_confirm(interaction, self._image_bytes, self._source, cid, name)


class ContainerSelectView(discord.ui.View):
    """Shown immediately when an image is posted. User picks or creates a container first."""

    def __init__(self, containers: list[dict], image_bytes: bytes, source_message: discord.Message):
        super().__init__(timeout=120)
        self._image_bytes = image_bytes
        self._source = source_message
        self._default = _last_container.get(source_message.author.id)  # (id, name) or None

        if containers:
            options = [
                discord.SelectOption(
                    label=c["name"],
                    value=str(c["id"]),
                    description=f"{c['type']} · {c['card_count']} cards",
                )
                for c in containers[:25]
            ]
            select = discord.ui.Select(placeholder="📦 Change container…", options=options, row=0)
            select.callback = self._on_select
            self.add_item(select)

        if self._default:
            use_btn = discord.ui.Button(
                label=f"Use: {self._default[1]}",
                style=discord.ButtonStyle.success,
                emoji="✅",
                row=1,
            )
            use_btn.callback = self._use_default
            self.add_item(use_btn)

        new_btn = discord.ui.Button(
            label="New container",
            style=discord.ButtonStyle.primary,
            emoji="➕",
            row=1,
        )
        new_btn.callback = self._new_container
        self.add_item(new_btn)

    async def _on_select(self, interaction: discord.Interaction):
        container_id = int(interaction.data["values"][0])
        c = await bot.db.get_container(container_id)
        if not c:
            self.stop()
            await interaction.response.send_message(
                "This container was deleted. Please try again.", ephemeral=True
            )
            return
        _last_container[self._source.author.id] = (container_id, c["name"])
        self.stop()
        await _do_scan_and_confirm(interaction, self._image_bytes, self._source, container_id, c["name"])

    async def _use_default(self, interaction: discord.Interaction):
        self.stop()
        await _do_scan_and_confirm(
            interaction, self._image_bytes, self._source, self._default[0], self._default[1]
        )

    async def _new_container(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.send_modal(NewContainerModal(self._image_bytes, self._source))


class NameCorrectionModal(discord.ui.Modal, title="Correct card name"):
    card_name = discord.ui.TextInput(label="Card name", placeholder="e.g. Lightning Bolt", max_length=100)
    set_code = discord.ui.TextInput(label="Set code (optional)", placeholder="e.g. M10", required=False, max_length=10)

    def __init__(self, image_bytes: bytes, source_message: discord.Message, container_id: int, container_name: str):
        super().__init__()
        self._image_bytes = image_bytes
        self._source = source_message
        self._container_id = container_id
        self._container_name = container_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        card, detected_lang = await bot.scryfall.resolve_card(
            self.card_name.value.strip(),
            self.set_code.value.strip() or None,
        )
        if not card:
            await interaction.followup.send(
                f'Card "{self.card_name.value}" not found on Scryfall.', ephemeral=True
            )
            return
        card["language"] = detected_lang or "en"
        card["container_id"] = self._container_id
        card["container_name"] = self._container_name
        lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
        embed = card_embed(card, title_prefix="Found — confirm?  ")
        embed.description = f"Language: {lang_flag}  |  Container: 📦 **{self._container_name}**"
        containers = await bot.db.list_containers()
        view = ScanConfirmView(card, self._source, self._image_bytes, containers)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class ScanConfirmView(discord.ui.View):
    def __init__(
        self,
        card: dict,
        source_message: discord.Message,
        image_bytes: bytes,
        containers: list[dict],
        match_method: str = "",
    ):
        super().__init__(timeout=120)
        self._card = card
        self._source = source_message
        self._image_bytes = image_bytes
        self._match_method = match_method

        # Row 1: container change select
        if containers:
            current_id = card.get("container_id")
            options = [
                discord.SelectOption(
                    label=c["name"][:100],
                    value=str(c["id"]),
                    description=f"{c.get('type', 'binder')} · {c['card_count']} cards",
                    default=(c["id"] == current_id),
                )
                for c in containers[:25]
            ]
            sel = discord.ui.Select(
                placeholder="📦 Change container for this & future scans…",
                options=options,
                row=1,
            )
            sel.callback = self._on_container
            self.add_item(sel)

        # Row 2: create a new container on the fly
        new_btn = discord.ui.Button(
            label="New container",
            style=discord.ButtonStyle.primary,
            emoji="➕",
            row=2,
        )
        new_btn.callback = self._new_container
        self.add_item(new_btn)

    def _build_embed(self) -> discord.Embed:
        lang_flag = LANG_EMOJI.get(self._card.get("language", "en"), "")
        container_name = self._card.get("container_name", "—")
        embed = card_embed(self._card, title_prefix="Found — confirm?  ")
        embed.description = (
            f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
            + (f"\n*{self._match_method}*" if self._match_method else "")
        )
        return embed

    async def _on_container(self, interaction: discord.Interaction):
        cid = int(interaction.data["values"][0])
        c = await bot.db.get_container(cid)
        if not c:
            await interaction.response.send_message("Container no longer exists.", ephemeral=True)
            return
        self._card["container_id"] = cid
        self._card["container_name"] = c["name"]
        _last_container[self._source.author.id] = (cid, c["name"])
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _new_container(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NewContainerScanModal(self))

    async def _save(self, interaction: discord.Interaction, foil: bool):
        self._card["foil"] = foil
        self._card.setdefault("condition", "NM")
        self._card.setdefault("quantity", 1)
        row_id = await bot.db.add_card(self._card, added_by=str(self._source.author.id))
        self._card["id"] = row_id
        lang_flag = LANG_EMOJI.get(self._card.get("language", "en"), "")
        foil_tag = " ✨" if foil else ""
        embed = card_embed(self._card, title_prefix="Added ✅  ")
        embed.description = f"ID **{row_id}** | {lang_flag}{foil_tag} | added by {self._source.author.mention}"
        self.clear_items()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Add", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._save(interaction, foil=False)

    @discord.ui.button(label="Add as foil", style=discord.ButtonStyle.secondary, emoji="✨", row=0)
    async def add_foil(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._save(interaction, foil=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="✖", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        self.clear_items()
        await interaction.response.edit_message(content="Skipped.", embed=None, view=self)


class NewContainerScanModal(discord.ui.Modal, title="New container"):
    cont_name = discord.ui.TextInput(label="Container name", placeholder="e.g. Binder 2", max_length=100)
    cont_type = discord.ui.TextInput(
        label="Type  (binder / box / deck / trade / other)",
        placeholder="binder",
        required=False,
        max_length=20,
    )

    def __init__(self, confirm_view: ScanConfirmView):
        super().__init__()
        self._cv = confirm_view

    async def on_submit(self, interaction: discord.Interaction):
        type_val = (self.cont_type.value or "binder").strip().lower()
        if type_val not in CONTAINER_TYPES:
            type_val = "binder"
        name = self.cont_name.value.strip()
        try:
            cid = await bot.db.create_container(name, type=type_val)
        except Exception:
            containers = await bot.db.list_containers()
            existing = next((c for c in containers if c["name"] == name), None)
            if not existing:
                await interaction.response.send_message("Could not create container.", ephemeral=True)
                return
            cid = existing["id"]
        self._cv._card["container_id"] = cid
        self._cv._card["container_name"] = name
        _last_container[self._cv._source.author.id] = (cid, name)
        self._cv.stop()
        containers = await bot.db.list_containers()
        new_view = ScanConfirmView(
            self._cv._card,
            self._cv._source,
            self._cv._image_bytes,
            containers,
            self._cv._match_method,
        )
        await interaction.response.edit_message(embed=new_view._build_embed(), view=new_view)


async def _handle_scan_attachment(message: discord.Message, attachment: discord.Attachment):
    image_bytes = await attachment.read()
    default = _last_container.get(message.author.id)

    if default:
        # Container already known — skip the selection step and scan immediately.
        container_id, container_name = default
        scanning_msg = await message.reply(
            f"🔍 Scanning… 📦 **{container_name}**", mention_author=False
        )
        await _do_scan_direct(scanning_msg, image_bytes, message, container_id, container_name)
    else:
        # No container known yet — ask first.
        containers = await bot.db.list_containers()
        view = ContainerSelectView(containers, image_bytes, message)
        await message.channel.send(
            f"{message.author.mention} 📦 Which container is this card going into?",
            view=view,
        )


class _ManualNameView(discord.ui.View):
    def __init__(self, image_bytes: bytes, source_message: discord.Message, container_id: int, container_name: str):
        super().__init__(timeout=120)
        self._image_bytes = image_bytes
        self._source = source_message
        self._container_id = container_id
        self._container_name = container_name

    @discord.ui.button(label="Enter card name", style=discord.ButtonStyle.primary, emoji="🔍")
    async def enter_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            NameCorrectionModal(self._image_bytes, self._source, self._container_id, self._container_name)
        )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Showcase channel: reply with the welcome menu whenever someone writes.
    if SHOWCASE_CHANNEL_ID and message.channel.id == SHOWCASE_CHANNEL_ID:
        embed = discord.Embed(
            title="Welcome to the MTG Collection!",
            description=(
                f"Hey {message.author.display_name}! "
                "Here's what you can explore — all replies are private to you."
            ),
            color=0x5865f2,
        )
        embed.set_thumbnail(url=message.author.display_avatar.url)
        await message.reply(embed=embed, view=WelcomeView(), mention_author=False)

    if SCAN_CHANNEL_ID is None or message.channel.id != SCAN_CHANNEL_ID:
        await bot.process_commands(message)
        return

    images = [
        a for a in message.attachments
        if (a.content_type or "").startswith("image/")
        or a.filename.lower().rsplit(".", 1)[-1] in _SCAN_IMAGE_EXTS
    ]
    if not images:
        await bot.process_commands(message)
        return

    for attachment in images:
        try:
            await _handle_scan_attachment(message, attachment)
        except Exception as exc:
            logger.error("_handle_scan_attachment error: %s", exc, exc_info=True)
            try:
                await message.channel.send(
                    f"{message.author.mention} ⚠️ Scan failed — try again or use `/add`.",
                    delete_after=30,
                )
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _configure_logging(debug=DEBUG_SCAN_PREVIEW)
    bot.run(TOKEN)
