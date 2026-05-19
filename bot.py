"""MTG Collection Manager — Discord bot (thin loader)."""

# ── Environment setup — must run before ANY library that may touch CUDA ──────
import os
import warnings
from dotenv import load_dotenv

load_dotenv()

# Load discord settings from config.json (env vars from .env / Docker take precedence)
try:
    from core.config import get_discord as _get_discord
    _disc = _get_discord()
    _MAP = {
        "token":               "DISCORD_TOKEN",
        "guild_id":            "DISCORD_GUILD_ID",
        "scan_channel_id":     "DISCORD_SCAN_CHANNEL_ID",
        "showcase_channel_id": "DISCORD_SHOWCASE_CHANNEL_ID",
        "guest_role":          "DISCORD_GUEST_ROLE",
        "collector_role":      "DISCORD_COLLECTOR_ROLE",
        "admin_role":          "DISCORD_ADMIN_ROLE",
    }
    for cfg_key, env_key in _MAP.items():
        if not os.environ.get(env_key) and _disc.get(cfg_key):
            os.environ[env_key] = str(_disc[cfg_key])
    del _disc, _MAP, _get_discord
except Exception:
    pass

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", message=".*pin_memory.*")
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import logging
import logging.handlers
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks

import aiohttp
import core.image_cache as image_cache
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
    formatter = logging.Formatter(fmt)
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    os.makedirs("logs", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        "logs/mtg_collection.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(file_handler)


TOKEN    = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DEBUG_SCAN_PREVIEW = os.getenv("DEBUG_SCAN_PREVIEW", "0") == "1"

ALL_COGS = [
    "cogs.admin",
    "cogs.import_export",
    "cogs.backup",
    "cogs.scan",
]

_READ_ONLY_COMMANDS = {"export"}

SCAN_CHANNEL_ID   = int(os.getenv("DISCORD_SCAN_CHANNEL_ID", 0)) or None


class MTGCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cmd = interaction.command
        if cmd is None:
            return True
        name = cmd.qualified_name
        if name not in _READ_ONLY_COMMANDS:
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
        asyncio.create_task(self._cache_images())

    async def _init_ocr(self):
        try:
            await scanner.init_ocr()
        except Exception as e:
            logger.error("OCR init failed: %s", e)

    async def _cache_images(self):
        await self.wait_until_ready()
        try:
            refs = await self.db.get_all_image_refs()
            missing = [(sid, url) for sid, url in refs if not image_cache.get_cached_path(sid)]
            if not missing:
                logger.info("Image cache: all %d images already cached", len(refs))
                return
            logger.info("Image cache: %d/%d images missing — downloading in background", len(missing), len(refs))
            async with aiohttp.ClientSession(headers={"Accept": "image/webp,image/*,*/*;q=0.8"}) as session:
                for scryfall_id, image_url in missing:
                    await image_cache.ensure_cached(scryfall_id, image_url, session=session)
                    await asyncio.sleep(0.5)  # 2 req/s — leave headroom for API calls sharing Scryfall's rate budget
            logger.info("Image cache: done")
        except Exception as exc:
            logger.error("Image cache task failed: %s", exc)

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
