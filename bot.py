"""MTG Collection Manager — Discord bot (thin loader)."""

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
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks

import core.scanner as scanner
from core.database import Database
from core.scryfall import ScryfallClient

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


TOKEN    = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DEBUG_SCAN_PREVIEW = os.getenv("DEBUG_SCAN_PREVIEW", "0") == "1"

ALL_COGS = [
    "cogs.containers",
    "cogs.collection",
    "cogs.admin",
    "cogs.import_export",
    "cogs.stats",
    "cogs.deck",
    "cogs.showcase",
    "cogs.backup",
    "cogs.scan",
]

_READ_ONLY_COMMANDS = {
    "search", "list", "stats", "export",
    "container list", "showcase",
}

DECK_CHANNEL_ID   = int(os.getenv("DISCORD_DECKBUILDER_CHANNEL_ID", 0)) or None
SEARCH_CHANNEL_ID = int(os.getenv("DISCORD_SEARCH_CHANNEL_ID",      0)) or None
SCAN_CHANNEL_ID   = int(os.getenv("DISCORD_SCAN_CHANNEL_ID",        0)) or None


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

        # Load all cogs
        for ext in ALL_COGS:
            await self.load_extension(ext)

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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _configure_logging(debug=DEBUG_SCAN_PREVIEW)
    bot.run(TOKEN)
