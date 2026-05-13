"""MTG Collection Manager — Discord bot."""

import asyncio
import io
import logging
import os
import sys
import warnings
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import card_index
import deckbuilder
import exporter as exp
import scanner
from database import Database
from scryfall import ScryfallClient

# Prevent matplotlib / some OCR libs from trying to open a display
os.environ.setdefault("MPLBACKEND", "Agg")
# Suppress torch DataLoader pin_memory warning on CPU-only machines
warnings.filterwarnings("ignore", message=".*pin_memory.*")

load_dotenv()
logger = logging.getLogger(__name__)


def _configure_logging() -> None:
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
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)

TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
SCAN_CHANNEL_ID = int(os.getenv("DISCORD_SCAN_CHANNEL_ID", 0)) or None
DECK_CHANNEL_ID = int(os.getenv("DISCORD_DECKBUILDER_CHANNEL_ID", 0)) or None
GUEST_ROLE        = os.getenv("DISCORD_GUEST_ROLE",     "")   # read-only commands
COLLECTOR_ROLE    = os.getenv("DISCORD_COLLECTOR_ROLE", "")   # add / scan / update
ADMIN_ROLE        = os.getenv("DISCORD_ADMIN_ROLE",     "")   # remove / container mgmt / index rebuild
DEBUG_SCAN_PREVIEW = os.getenv("DEBUG_SCAN_PREVIEW", "0") == "1"  # send processed image for debugging

CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]
LANG_EMOJI = {"en": "🇬🇧", "de": "🇩🇪"}
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


async def require_guest(interaction: discord.Interaction) -> bool:
    """Read-only commands. Open to all when DISCORD_GUEST_ROLE is not configured."""
    if not GUEST_ROLE:
        return True
    if not isinstance(interaction.user, discord.Member):
        return True
    if _member_has_any_role(interaction.user, GUEST_ROLE, COLLECTOR_ROLE, ADMIN_ROLE):
        return True
    await _deny(interaction, GUEST_ROLE)
    return False


async def require_collector(interaction: discord.Interaction) -> bool:
    """Add/modify commands. Open to all when DISCORD_COLLECTOR_ROLE is not configured."""
    if not COLLECTOR_ROLE:
        return True
    if not isinstance(interaction.user, discord.Member):
        return True
    if _member_has_any_role(interaction.user, COLLECTOR_ROLE, ADMIN_ROLE):
        return True
    await _deny(interaction, COLLECTOR_ROLE)
    return False


async def require_admin(interaction: discord.Interaction) -> bool:
    """Destructive/admin commands. Open to all when DISCORD_ADMIN_ROLE is not configured."""
    if not ADMIN_ROLE:
        return True
    if not isinstance(interaction.user, discord.Member):
        return True
    if _member_has_any_role(interaction.user, ADMIN_ROLE):
        return True
    await _deny(interaction, ADMIN_ROLE)
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

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
    embed.add_field(name="Set", value=f"{card.get('set_name', '?')} ({card.get('set_code', '?').upper()})", inline=True)
    embed.add_field(name="№", value=card.get("collector_number") or "—", inline=True)
    embed.add_field(name="Rarity", value=rarity.capitalize(), inline=True)
    embed.add_field(name="Language", value=lang_flag, inline=True)

    cond = card.get("condition", "NM")
    foil = " ✨" if card.get("foil") else ""
    container = card.get("container_name") or "—"
    embed.add_field(name="Condition", value=f"{cond}{foil}", inline=True)
    embed.add_field(name="Container", value=container, inline=True)

    price_parts = []
    if card.get("price_eur"):
        price_parts.append(f"€{card['price_eur']:.2f}")
    if card.get("price_usd"):
        price_parts.append(f"${card['price_usd']:.2f}")
    embed.add_field(name="Price", value=" / ".join(price_parts) if price_parts else "—", inline=True)

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


def paginate_embeds(cards: list[dict], page: int, per_page: int = 10) -> tuple[discord.Embed, int]:
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
        embed.add_field(name=f"[{c['id']}] {name}", value=val, inline=False)

    return embed, pages


# ──────────────────────────────────────────────────────────────────────────────
# Bot
# ──────────────────────────────────────────────────────────────────────────────

class MTGCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cmd = interaction.command
        if cmd is None:
            return True
        is_deck = cmd.qualified_name.startswith("deck")
        if is_deck:
            if DECK_CHANNEL_ID and interaction.channel_id != DECK_CHANNEL_ID:
                await interaction.response.send_message(
                    f"Deck commands only work in <#{DECK_CHANNEL_ID}>.", ephemeral=True
                )
                return False
        else:
            if SCAN_CHANNEL_ID and interaction.channel_id != SCAN_CHANNEL_ID:
                await interaction.response.send_message(
                    f"This bot only works in <#{SCAN_CHANNEL_ID}>.", ephemeral=True
                )
                return False
        return True


class MTGBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, tree_cls=MTGCommandTree)
        self.db = Database()
        self.scryfall = ScryfallClient()
        self.hash_cache: list[dict] = []

    async def setup_hook(self):
        await self.db.initialize()
        self.hash_cache = await self.db.get_all_hashes()
        logger.info("Hash index loaded: %d entries", len(self.hash_cache))

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

        new_set_check.start()
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
        await self.change_presence(activity=discord.Game(name="/help • MTG Collection"))


bot = MTGBot()


# ──────────────────────────────────────────────────────────────────────────────
# Background: daily new-set check
# ──────────────────────────────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def new_set_check():
    if not SCAN_CHANNEL_ID:
        return
    try:
        new_sets = await card_index.check_new_sets(bot.db)
    except Exception as e:
        logger.error("New-set check failed: %s", e)
        return
    if not new_sets:
        return
    channel = bot.get_channel(SCAN_CHANNEL_ID)
    if not channel:
        return
    shown = new_sets[:20]
    extra = len(new_sets) - len(shown)
    lines = "\n".join(
        f"• **{s['name']}** (`{s['code']}`) — released {s['released_at']}"
        for s in shown
    )
    suffix = f"\n…and {extra} more." if extra else ""
    msg = (
        f"**{len(new_sets)} new MTG set(s)** found on Scryfall not yet in the hash index:\n"
        f"{lines}{suffix}\nRun `/index update` to add them."
    )
    await channel.send(msg[:1990])


@new_set_check.before_loop
async def _before_new_set_check():
    await bot.wait_until_ready()


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


@container_group.command(name="create", description="Create a new container")
@app_commands.describe(name="Container name", type="Container type", description="Optional description")
@app_commands.choices(type=[app_commands.Choice(name=t, value=t) for t in CONTAINER_TYPES])
async def container_create(
    interaction: discord.Interaction,
    name: str,
    type: str = "binder",
    description: str = "",
):
    if not await require_collector(interaction):
        return
    try:
        cid = await bot.db.create_container(name, description, type)
    except Exception:
        await interaction.response.send_message(
            f'A container named **{name}** already exists.', ephemeral=True
        )
        return
    await interaction.response.send_message(
        f'📦 Container **{name}** (`{type}`) created with ID **{cid}**.'
    )


@container_group.command(name="list", description="List all containers")
async def container_list(interaction: discord.Interaction):
    if not await require_guest(interaction):
        return
    containers = await bot.db.list_containers()
    if not containers:
        await interaction.response.send_message(
            "No containers yet. Use `/container create` to make one.", ephemeral=True
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
    await interaction.response.send_message(embed=embed)


@container_group.command(name="delete", description="Delete a container (cards are kept, container link is removed)")
@app_commands.describe(id="Container ID")
async def container_delete(interaction: discord.Interaction, id: int):
    if not await require_admin(interaction):
        return
    c = await bot.db.get_container(id)
    if not c:
        await interaction.response.send_message(f"No container with ID {id}.", ephemeral=True)
        return
    view = ConfirmView(timeout=30)
    await interaction.response.send_message(
        f'Delete container **{c["name"]}**? Cards in it will not be deleted.', view=view, ephemeral=True
    )
    await view.wait()
    if view.confirmed:
        await bot.db.delete_container(id)
        await interaction.edit_original_response(content=f'Container **{c["name"]}** deleted.', view=None)
    else:
        await interaction.edit_original_response(content="Cancelled.", view=None)


@container_group.command(name="rename", description="Rename a container")
@app_commands.describe(id="Container ID", name="New name")
async def container_rename(interaction: discord.Interaction, id: int, name: str):
    if not await require_admin(interaction):
        return
    ok = await bot.db.rename_container(id, name)
    if ok:
        await interaction.response.send_message(f'Container renamed to **{name}**.')
    else:
        await interaction.response.send_message(f"No container with ID {id}.", ephemeral=True)


bot.tree.add_command(container_group)


# ──────────────────────────────────────────────────────────────────────────────
# /index  (status / update / check)
# ──────────────────────────────────────────────────────────────────────────────

index_group = app_commands.Group(name="index", description="Manage the visual card hash index")


@index_group.command(name="status", description="Show hash index statistics")
async def index_status(interaction: discord.Interaction):
    if not await require_guest(interaction):
        return
    count = await bot.db.get_hash_count()
    last = await bot.db.get_index_meta("last_built_at") or "never"
    embed = discord.Embed(title="Card Hash Index", color=0x9B59B6)
    embed.add_field(name="Indexed cards", value=str(count), inline=True)
    embed.add_field(name="In-memory cache", value=str(len(bot.hash_cache)), inline=True)
    embed.add_field(name="Last built", value=last[:19] if last != "never" else "never", inline=False)
    await interaction.response.send_message(embed=embed)


def _as_editable_message(interaction: discord.Interaction, webhook_msg) -> discord.PartialMessage:
    """
    Wrap a WebhookMessage as a PartialMessage editable via the bot token.
    Unlike WebhookMessage.edit() (15-min interaction token), PartialMessage.edit()
    uses the bot token and never expires — critical for long-running index builds.
    get_partial_message() constructs the object locally; no API call, no extra perms.
    """
    return interaction.channel.get_partial_message(webhook_msg.id)


@index_group.command(name="update", description="Download new card images and update the hash index")
async def index_update(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(thinking=True)
    # Send initial message via the webhook (interaction token), then immediately
    # re-fetch it as a plain Message so all subsequent edits use the bot token.
    webhook_msg = await interaction.followup.send("Starting index build…", wait=True)
    progress_msg = _as_editable_message(interaction, webhook_msg)

    async def progress(done, total, indexed, status):
        pct = int(done / total * 100) if total else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        try:
            await progress_msg.edit(
                content=f"`[{bar}]` {pct}%  —  {status}\n{indexed} new cards indexed so far."
            )
        except discord.errors.HTTPException:
            pass  # message deleted or bot lost channel access — skip silently

    newly = await card_index.build_index(bot.db, progress_cb=progress)
    bot.hash_cache = await bot.db.get_all_hashes()
    await progress_msg.edit(
        content=(
            f"Index build complete. **{newly}** new card(s) indexed.\n"
            f"Cache reloaded — {len(bot.hash_cache)} total entries."
        )
    )


@index_group.command(name="check", description="List Scryfall sets not yet in the hash index")
async def index_check(interaction: discord.Interaction):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    new_sets = await card_index.check_new_sets(bot.db)
    if not new_sets:
        await interaction.followup.send("All known sets are already indexed.", ephemeral=True)
        return
    shown = new_sets[:20]
    extra = len(new_sets) - len(shown)
    lines = "\n".join(
        f"• **{s['name']}** (`{s['code']}`) — released {s['released_at']}"
        for s in shown
    )
    suffix = f"\n…and {extra} more." if extra else ""
    msg = f"**{len(new_sets)} set(s) not yet indexed:**\n{lines}{suffix}\n\nRun `/index update` to add them."
    await interaction.followup.send(msg[:1990])


@index_group.command(name="rebuild", description="Wipe the hash index and re-download all card images from scratch")
async def index_rebuild(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    await interaction.response.defer(thinking=True)
    old_count = await bot.db.get_hash_count()
    webhook_msg = await interaction.followup.send(
        f"Clearing {old_count} existing entries and rebuilding from scratch…", wait=True
    )
    progress_msg = _as_editable_message(interaction, webhook_msg)
    await bot.db.clear_card_hashes()
    bot.hash_cache = []

    async def progress(done, total, indexed, status):
        pct = int(done / total * 100) if total else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        try:
            await progress_msg.edit(
                content=f"`[{bar}]` {pct}%  —  {status}\n{indexed} cards indexed so far."
            )
        except discord.errors.HTTPException:
            pass

    newly = await card_index.build_index(bot.db, progress_cb=progress)
    bot.hash_cache = await bot.db.get_all_hashes()
    await progress_msg.edit(
        content=(
            f"Rebuild complete. **{newly}** card(s) indexed.\n"
            f"Cache reloaded — {len(bot.hash_cache)} total entries."
        )
    )


bot.tree.add_command(index_group)


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
            container_name = c["name"] if c else None
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
    embed = card_embed(card, title_prefix="Added ✅  ")
    new_tag = "  *(container created)*" if container and not container.isdigit() and container_name == container else ""
    id_range = f"IDs **{ids[0]}–{ids[-1]}**" if len(ids) > 1 else f"ID **{ids[0]}**"
    embed.description = f"Saved as {id_range} ({len(ids)} cop{'y' if len(ids)==1 else 'ies'}) | Language {lang_flag}{new_tag}"
    await interaction.followup.send(embed=embed)


# ──────────────────────────────────────────────────────────────────────────────
# /scan  — add card(s) by uploading a photo
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="scan", description="Add a card by uploading a photo (OCR)")
@app_commands.describe(
    image="Photo of your MTG card",
    condition="Card condition",
    foil="Is this a foil?",
    quantity="How many copies",
    language="Override detected language",
)
@app_commands.choices(condition=[app_commands.Choice(name=c, value=c) for c in CONDITIONS])
@app_commands.choices(language=[
    app_commands.Choice(name="English", value="en"),
    app_commands.Choice(name="German / Deutsch", value="de"),
])
async def cmd_scan(
    interaction: discord.Interaction,
    image: discord.Attachment,
    condition: str = "NM",
    foil: bool = False,
    quantity: int = 1,
    language: str = "",
):
    if not await require_collector(interaction):
        return
    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message("Please attach an image file.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    image_bytes = await image.read()

    # Hash match + OCR in parallel (same logic as the auto-scan path)
    best = None
    if bot.hash_cache:
        best = await card_index.find_best_match(image_bytes, bot.hash_cache)

    extracted_name: Optional[str] = await asyncio.to_thread(scanner.extract_name, image_bytes)

    hash_card: Optional[dict] = None
    hash_lang = "en"
    if best:
        hash_card = await bot.scryfall.get_by_id(best["scryfall_id"])
        hash_lang = best.get("lang", "en")

    ocr_card: Optional[dict] = None
    ocr_lang = "unknown"
    if extracted_name:
        ocr_card, ocr_lang = await bot.scryfall.resolve_card(extracted_name)

    card: Optional[dict] = None
    detected_lang_final = "en"
    match_note = ""

    if ocr_card:
        card = ocr_card
        detected_lang_final = ocr_lang if ocr_lang != "unknown" else "en"
        match_note = f'OCR [{ocr_lang}]: "{extracted_name}"'
        if hash_card:
            same = (
                bool(ocr_card.get("oracle_id")) and ocr_card.get("oracle_id") == hash_card.get("oracle_id")
            ) or ocr_card.get("name_en", "").lower() == hash_card.get("name_en", "").lower()
            match_note += f'  •  visual {"confirmed" if same else hash_card.get("name_en", "?") + " (mismatch)"} (d={best["hash_distance"]})'
            if not same:
                logger.info("OCR/hash mismatch: OCR=%s, hash=%s → keeping OCR result",
                            ocr_card.get("name_en"), hash_card.get("name_en"))
    elif hash_card:
        card = hash_card
        detected_lang_final = hash_lang
        match_note = f"visual (d={best['hash_distance']})"
        if extracted_name:
            match_note += f'  •  OCR: "{extracted_name}" (no Scryfall match)'

    if not card:
        if not scanner.ocr_available() and not bot.hash_cache:
            await interaction.followup.send(
                "OCR is not available and no hash index loaded. Use `/add` instead.", ephemeral=True
            )
        elif extracted_name:
            await interaction.followup.send(
                f'Could not match **"{extracted_name}"** on Scryfall. '
                f'Try `/add {extracted_name}` with the exact name.',
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "Could not read the card name from the image. Try a clearer photo, or use `/add <name>`.",
                ephemeral=True,
            )
        return

    card["language"] = language or detected_lang_final or "en"
    card["condition"] = condition
    card["foil"] = foil
    card["quantity"] = 1

    copies = max(1, quantity)
    ids = []
    for _ in range(copies):
        ids.append(await bot.db.add_card(card, added_by=str(interaction.user.id)))

    card["id"] = ids[0]
    lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
    embed = card_embed(card, title_prefix="Scanned ✅  ")
    id_range = f"IDs **{ids[0]}–{ids[-1]}**" if len(ids) > 1 else f"ID **{ids[0]}**"
    embed.description = (
        f"Saved as {id_range} ({len(ids)} cop{'y' if len(ids)==1 else 'ies'}) | Language {lang_flag}"
        + (f"\n*{match_note}*" if match_note else "")
    )
    await interaction.followup.send(embed=embed)


# ──────────────────────────────────────────────────────────────────────────────
# /search
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="search", description="Full-text search across all card fields")
@app_commands.describe(query="Search terms (name, type, oracle text, set, …)")
async def cmd_search(interaction: discord.Interaction, query: str):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    results = await bot.db.search(query, limit=15)
    if not results:
        await interaction.followup.send(f"No results for **{query}**.", ephemeral=True)
        return
    embed, _ = paginate_embeds(results, 1, per_page=15)
    embed.title = f'Search: "{query}"  —  {len(results)} result(s)'
    await interaction.followup.send(embed=embed)


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
    cards = await bot.db.list_cards(
        limit=200, offset=0, sort=sort, language=language or None
    )
    if not cards:
        await interaction.followup.send("Your collection is empty.", ephemeral=True)
        return
    embed, pages = paginate_embeds(cards, page)
    await interaction.followup.send(embed=embed)


# ──────────────────────────────────────────────────────────────────────────────
# /card
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="card", description="Show details for a card by its collection ID")
@app_commands.describe(id="Collection ID (shown in /list)")
async def cmd_card(interaction: discord.Interaction, id: int):
    if not await require_guest(interaction):
        return
    card = await bot.db.get_card(id)
    if not card:
        await interaction.response.send_message(f"No card with ID {id}.", ephemeral=True)
        return
    await interaction.response.send_message(embed=card_embed(card))


# ──────────────────────────────────────────────────────────────────────────────
# /remove
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="remove", description="Remove a card from your collection by ID")
@app_commands.describe(id="Collection ID to remove")
async def cmd_remove(interaction: discord.Interaction, id: int):
    if not await require_admin(interaction):
        return
    card = await bot.db.get_card(id)
    if not card:
        await interaction.response.send_message(f"No card with ID {id}.", ephemeral=True)
        return

    view = ConfirmView(timeout=30)
    await interaction.response.send_message(
        f"Remove **{card['name_en']}** (ID {id}) from your collection?",
        view=view,
        ephemeral=True,
    )
    await view.wait()
    if view.confirmed:
        await bot.db.remove_card(id)
        await interaction.edit_original_response(content=f"Removed **{card['name_en']}** (ID {id}).", view=None)
    else:
        await interaction.edit_original_response(content="Cancelled.", view=None)


# ──────────────────────────────────────────────────────────────────────────────
# /update
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="update", description="Update a field on a collection entry")
@app_commands.describe(
    id="Collection ID",
    field="Field to update",
    value="New value",
)
@app_commands.choices(field=[
    app_commands.Choice(name="condition", value="condition"),
    app_commands.Choice(name="foil (0/1)", value="foil"),
    app_commands.Choice(name="language (en/de)", value="language"),
    app_commands.Choice(name="notes", value="notes"),
    app_commands.Choice(name="price_eur", value="price_eur"),
    app_commands.Choice(name="price_usd", value="price_usd"),
])
async def cmd_update(interaction: discord.Interaction, id: int, field: str, value: str):
    if not await require_collector(interaction):
        return
    card = await bot.db.get_card(id)
    if not card:
        await interaction.response.send_message(f"No card with ID {id}.", ephemeral=True)
        return

    coerced: object = value
    if field in ("foil",):
        coerced = 1 if value.lower() in ("1", "yes", "true", "foil") else 0
    elif field in ("price_eur", "price_usd"):
        try:
            coerced = float(value)
        except ValueError:
            await interaction.response.send_message("Price must be a number.", ephemeral=True)
            return

    ok = await bot.db.update_card(id, field, coerced)
    if ok:
        await interaction.response.send_message(
            f"Updated **{card['name_en']}** (ID {id}): `{field}` → `{coerced}`"
        )
    else:
        await interaction.response.send_message(f"Could not update field `{field}`.", ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# /export
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="export", description="Export your entire collection as CSV or JSON")
@app_commands.describe(format="File format")
@app_commands.choices(format=[
    app_commands.Choice(name="CSV (Excel-compatible)", value="csv"),
    app_commands.Choice(name="JSON", value="json"),
])
async def cmd_export(interaction: discord.Interaction, format: str = "csv"):
    if not await require_guest(interaction):
        return
    await interaction.response.defer(thinking=True)
    cards = await bot.db.get_all()
    if not cards:
        await interaction.followup.send("Your collection is empty.", ephemeral=True)
        return

    if format == "json":
        content = exp.to_json(cards).encode("utf-8")
        filename = "collection.json"
        mime = "application/json"
    else:
        content = exp.to_csv(cards).encode("utf-8")
        filename = "collection.csv"
        mime = "text/csv"

    file = discord.File(io.BytesIO(content), filename=filename)
    await interaction.followup.send(
        f"Exported **{len(cards)}** entries as `{filename}`.",
        file=file,
    )


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

    await interaction.followup.send(embed=embed)


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
    embed.set_footer(text="Full deck list attached · confirm with Add buttons")
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
        fname = (commander.get("name_en") or "commander").replace(" ", "_").replace(",", "").lower()
        file = discord.File(io.BytesIO(decklist), filename=f"{fname}_deck.txt")
        await interaction.edit_original_response(embed=embed, view=None, attachments=[file])


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
        await interaction.followup.send(embed=embed, file=file)


bot.tree.add_command(deck_group)


# ──────────────────────────────────────────────────────────────────────────────
# /help
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="help", description="Show available commands")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(title="MTG Collection Manager — All Commands", color=0xE74C3C)

    embed.add_field(
        name="➕ Adding cards",
        value=(
            "`/add <name>` — add by name, auto-detects EN/DE; `quantity` creates N separate entries\n"
            "`/scan <image>` — attach a photo; visual hash match first, OCR fallback\n"
            "Drop an image in this channel — bot scans it instantly (no command needed)\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔍 Viewing",
        value=(
            "`/list` — browse full collection (chaos sort by default)\n"
            "`/list sort:Name` — sort by name, set, CMC, or date added\n"
            "`/card <id>` — full details for one card\n"
            "`/search <query>` — full-text search across name, type, oracle text, set, notes, …\n"
            "`/stats` — cards by language & foil, rarity breakdown, value, top 5 most valuable\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="✏️ Editing",
        value=(
            "`/update <id> <field> <value>` — edit `condition`, `foil`, `language`, `notes`, `price_eur`, `price_usd`\n"
            "`/remove <id>` — delete a card entry\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="📦 Containers",
        value=(
            "`/container create <name>` — create a binder, box, deck, trade pile, …\n"
            "`/container list` — all containers with card count and total value\n"
            "`/container rename <id> <name>` — rename a container\n"
            "`/container delete <id>` — remove a container (cards are kept)\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="📤 Export",
        value="`/export` — download your full collection as CSV or JSON\n",
        inline=False,
    )
    embed.add_field(
        name="🖼️ Visual hash index",
        value=(
            "`/index status` — indexed card count, cache size, last build date\n"
            "`/index check` — list Scryfall sets not yet in the index\n"
            "`/index update` — download images for new sets and add them to the index\n"
            "`/index rebuild` — wipe the index and re-download everything from scratch\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="🃏 Deckbuilder (deckbuilder channel only)",
        value=(
            "`/deck propose format:Commander` — score commanders by collection synergy, pick one, get a 100-card proposal\n"
            "`/deck propose format:Timeless` — auto-detect dominant strategy, build a 60-card Timeless deck\n"
            "`/deck propose format:Standard` — same for Standard format\n"
            "Each proposal shows key cards with their container location + full deck list as `.txt`\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="🌀 Chaos sort order",
        value=(
            "W → U → B → R → G → Multicolor → Colorless → Land\n"
            "Within each color: Creature → Instant → Sorcery → Enchantment → Artifact → Planeswalker\n"
            "Then ascending CMC, then name A–Z"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# Confirmation UI
# ──────────────────────────────────────────────────────────────────────────────

class ConfirmView(discord.ui.View):
    def __init__(self, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.confirmed = False

    @discord.ui.button(label="Yes, remove", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()


# ──────────────────────────────────────────────────────────────────────────────
# Auto-scan channel  (drop a photo → bot processes it instantly)
# ──────────────────────────────────────────────────────────────────────────────

# Per-user last-used container: user_id -> (container_id, container_name)
_last_container: dict[int, tuple[int, str]] = {}


async def _do_scan_and_confirm(
    interaction: discord.Interaction,
    image_bytes: bytes,
    source_message: discord.Message,
    container_id: int,
    container_name: str,
):
    """Hash match + OCR → Scryfall lookup → show card confirmation. Caller must not have responded yet."""
    await interaction.response.defer(thinking=True)

    # Debug: show the processed image so crop quality can be verified
    if DEBUG_SCAN_PREVIEW:
        preview = scanner.get_isolated_preview(image_bytes)
        if preview:
            await interaction.followup.send(
                "🔍 **Debug preview** — isolated card + OCR name zone (red box):",
                file=discord.File(io.BytesIO(preview), filename="debug_preview.jpg"),
                ephemeral=True,
            )

    # ── Step 1: visual hash match ─────────────────────────────────────────
    hash_card: Optional[dict] = None
    hash_lang = "en"
    hash_dist = 0
    if bot.hash_cache:
        logger.info("Hash match attempt (cache: %d entries)", len(bot.hash_cache))
        best = await card_index.find_best_match(image_bytes, bot.hash_cache)
        if best:
            hash_card = await bot.scryfall.get_by_id(best["scryfall_id"])
            hash_lang = best.get("lang", "en")
            hash_dist = best.get("hash_distance", 0)
    else:
        logger.warning("Hash match skipped: cache empty — run /index update first")

    # ── Step 2: OCR — always run for language detection ───────────────────
    extracted: Optional[str] = await asyncio.to_thread(scanner.extract_name, image_bytes)
    if extracted:
        logger.info("OCR extracted: '%s'", extracted)
    else:
        logger.info("OCR: no text extracted")

    # Resolve OCR text against Scryfall (tries DE first, then EN → gives language)
    ocr_card: Optional[dict] = None
    ocr_lang = "unknown"
    if extracted:
        ocr_card, ocr_lang = await bot.scryfall.resolve_card(extracted)

    # ── Step 3: combine — OCR primary, hash secondary ────────────────────
    # OCR reads the text printed on the physical card → most accurate for
    # both identity and language.  Hash is a fast fallback when OCR fails.
    card: Optional[dict] = None
    detected_lang = "en"
    method_parts: list[str] = []

    if ocr_card:
        card = ocr_card
        detected_lang = ocr_lang if ocr_lang != "unknown" else "en"
        method_parts.append(f'OCR [{ocr_lang}]: "{extracted}"')

        if hash_card:
            same = (
                bool(ocr_card.get("oracle_id"))
                and ocr_card.get("oracle_id") == hash_card.get("oracle_id")
            ) or (
                ocr_card.get("name_en", "").lower() == hash_card.get("name_en", "").lower()
            )
            if same:
                method_parts.append(f"visual confirmed (d={hash_dist})")
            else:
                logger.info(
                    "OCR/hash mismatch: OCR=%s, hash=%s → keeping OCR result",
                    ocr_card.get("name_en"), hash_card.get("name_en"),
                )
                method_parts.append(f"visual: {hash_card.get('name_en', '?')} (d={hash_dist}, mismatch)")

    elif hash_card:
        # OCR found nothing — fall back to visual hash
        card = hash_card
        detected_lang = hash_lang
        method_parts.append(f"visual (d={hash_dist})")
        if extracted:
            method_parts.append(f'OCR: "{extracted}" (no Scryfall match)')

    if not card:
        view = _ManualNameView(image_bytes, source_message, container_id, container_name)
        if not scanner.ocr_available() and not bot.hash_cache:
            msg = "OCR not available and no hash index loaded. Use `/add <name>` instead."
        elif extracted:
            msg = f'Could not match **"{extracted}"** on Scryfall. Enter the name manually.'
        else:
            msg = "Could not read the card. Enter the name manually."
        await interaction.followup.send(msg, view=view, ephemeral=True)
        return

    card["language"] = detected_lang or "en"
    card["container_id"] = container_id
    card["container_name"] = container_name
    lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
    embed = card_embed(card, title_prefix="Found — confirm?  ")
    match_method = "  •  ".join(method_parts)
    embed.description = (
        f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
        + (f"\n*{match_method}*" if match_method else "")
    )
    view = ScanConfirmView(card, source_message, image_bytes)
    await interaction.followup.send(embed=embed, view=view)


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
        view = ScanConfirmView(card, self._source, self._image_bytes)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class ScanConfirmView(discord.ui.View):
    def __init__(self, card: dict, source_message: discord.Message, image_bytes: bytes):
        super().__init__(timeout=120)
        self._card = card
        self._source = source_message
        self._image_bytes = image_bytes

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

    @discord.ui.button(label="Add", style=discord.ButtonStyle.success, emoji="✅")
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._save(interaction, foil=False)

    @discord.ui.button(label="Add as foil", style=discord.ButtonStyle.secondary, emoji="✨")
    async def add_foil(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._save(interaction, foil=True)

    @discord.ui.button(label="Wrong card?", style=discord.ButtonStyle.secondary, emoji="🔍")
    async def wrong_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            NameCorrectionModal(
                self._image_bytes, self._source,
                self._card.get("container_id"), self._card.get("container_name", "—"),
            )
        )

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="✖")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.clear_items()
        await interaction.response.edit_message(content="Skipped.", embed=None, view=self)


async def _handle_scan_attachment(message: discord.Message, attachment: discord.Attachment):
    image_bytes = await attachment.read()
    containers = await bot.db.list_containers()
    view = ContainerSelectView(containers, image_bytes, message)
    default = _last_container.get(message.author.id)
    if default:
        prompt = f"📦 Last used: **{default[1]}** — use it or pick another."
    else:
        prompt = "📦 Which container is this card going into?"
    await message.channel.send(f"{message.author.mention} {prompt}", view=view)


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

    if SCAN_CHANNEL_ID is None or message.channel.id != SCAN_CHANNEL_ID:
        await bot.process_commands(message)
        return

    images = [
        a for a in message.attachments
        if a.content_type and a.content_type.startswith("image/")
    ]
    if not images:
        await bot.process_commands(message)
        return

    for attachment in images:
        await _handle_scan_attachment(message, attachment)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _configure_logging()
    bot.run(TOKEN)
