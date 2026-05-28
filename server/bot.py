"""MTG Collection Manager — Discord bot (autoscan only)."""

import sys
from pathlib import Path

# Keep project root on sys.path so core/ and cogs/ resolve regardless of
# which directory Python was started from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import warnings
import core.config as _cfg

_cfg.inject_env()
del _cfg

os.environ.setdefault("MPLBACKEND", "Agg")
warnings.filterwarnings("ignore", message=".*pin_memory.*")

import asyncio
import logging
import logging.handlers

import discord
from discord.ext import commands

import aiohttp
import core.image_cache as image_cache
import core.scanner as scanner
from core.database import Database

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

    from core.config import DATA_DIR
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        str(log_dir / "mtg_collection.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(file_handler)


TOKEN              = os.environ["DISCORD_TOKEN"]
DEBUG_SCAN_PREVIEW = os.getenv("DEBUG_SCAN_PREVIEW", "0") == "1"

ALL_COGS = ["cogs.scan"]


class MTGBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.db = Database()

    async def setup_hook(self):
        await self.db.initialize()

        for ext in ALL_COGS:
            await self.load_extension(ext)

        # Load OCR models in background — slow on first run, must not block startup
        asyncio.create_task(self._init_ocr())
        asyncio.create_task(self._cache_images())

    async def _init_ocr(self):
        try:
            await scanner.init_ocr()
        except Exception as e:
            logger.error("OCR init failed: %s", e)

    async def _cache_images(self):
        """Download any missing card images to the local cache in the background."""
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
                    await asyncio.sleep(0.5)  # 2 req/s — leave headroom for Scryfall rate limit
            logger.info("Image cache: done")
        except Exception as exc:
            logger.error("Image cache task failed: %s", exc)

    async def close(self):
        await self.db.close()
        await super().close()

    async def on_ready(self):
        logger.info("Logged in as %s (id=%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game(name="📦 MTG Collection"))


bot = MTGBot()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _configure_logging(debug=DEBUG_SCAN_PREVIEW)
    bot.run(TOKEN)
