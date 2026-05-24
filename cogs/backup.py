"""Backup cog: /backup create/restore + RestoreConfirmView."""
from __future__ import annotations

import asyncio
import gzip
import io
import lzma
import logging
import pathlib
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from core.database import Database
from cogs.auth import require_admin

logger = logging.getLogger(__name__)

from core.config import DATA_DIR as _DATA_DIR
BACKUP_DIR = pathlib.Path(os.getenv("BACKUP_DIR", str(_DATA_DIR / "backups")))

_MAX_UPLOAD_BYTES  = 64 * 1024 * 1024   # 64 MB raw upload
_MAX_RESTORE_BYTES = 256 * 1024 * 1024  # 256 MB after decompression


class RestoreConfirmView(discord.ui.View):
    def __init__(self, data: bytes, original_user_id: int):
        super().__init__(timeout=60)
        self._data = data
        self._original_user_id = original_user_id

    @discord.ui.button(label="Yes, restore", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self._original_user_id:
            await interaction.response.send_message(
                "❌ Only the user who initiated the restore can confirm it.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.defer()
        if interaction.client.db._restore_lock.locked():
            await interaction.edit_original_response(
                content="A restore is already in progress. Please wait.", embed=None, view=None
            )
            return
        await interaction.edit_original_response(
            content="Restoring database, please wait...", embed=None, view=None
        )
        try:
            await interaction.client.db.restore_from_bytes(self._data)
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


class BackupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    backup_group = app_commands.Group(
        name="backup", description="Database backup and restore (admin only)"
    )

    @backup_group.command(name="create", description="Download the current database as a backup file")
    async def backup_create(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await interaction.edit_original_response(content="Creating backup, please wait...")
        try:
            data = await interaction.client.db.backup_bytes()
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

        size_raw_mb = len(data) / 1024 / 1024

        # Compress for Discord upload — lzma/xz gives much better ratios than gzip for SQLite
        await interaction.edit_original_response(content="Compressing for upload…")
        xz_data = await asyncio.to_thread(lambda: lzma.compress(data, preset=6))
        xz_filename = base_filename + ".xz"
        size_xz_mb = len(xz_data) / 1024 / 1024

        await interaction.edit_original_response(
            content=f"Backup saved on server: `{local_path}` ({size_raw_mb:.1f} MB)\nUploading compressed copy…"
        )
        try:
            await interaction.followup.send(
                content=f"Compressed backup — `{xz_filename}` ({size_xz_mb:.2f} MB).",
                file=discord.File(io.BytesIO(xz_data), filename=xz_filename),
                ephemeral=True,
            )
            await interaction.edit_original_response(
                content=f"Backup saved on server: `{local_path}` ({size_raw_mb:.1f} MB) ✅"
            )
        except discord.HTTPException as exc:
            logger.error("Backup upload failed: %s", exc)
            await interaction.edit_original_response(
                content=(
                    f"Backup saved on server: `{local_path}` ({size_raw_mb:.1f} MB)\n"
                    f"⚠️ Could not upload to Discord ({size_xz_mb:.2f} MB compressed — "
                    f"server file size limit exceeded). Retrieve the backup directly from the server."
                )
            )

    @backup_group.command(name="restore", description="Restore the database from a backup file")
    @app_commands.describe(file="A .db backup file previously created with /backup create")
    async def backup_restore(self, interaction: discord.Interaction, file: discord.Attachment):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        if not (file.filename.endswith(".db") or file.filename.endswith(".db.gz") or file.filename.endswith(".db.xz")):
            await interaction.followup.send("Please attach a `.db`, `.db.gz`, or `.db.xz` backup file.", ephemeral=True)
            return
        if file.size > _MAX_UPLOAD_BYTES:
            await interaction.followup.send("❌ Attachment too large (max 64 MB).", ephemeral=True)
            return
        await interaction.edit_original_response(content="Reading backup file…")
        raw = await file.read()
        if file.filename.endswith(".xz"):
            await interaction.edit_original_response(content="Decompressing backup…")
            data = await asyncio.to_thread(lzma.decompress, raw)
            if len(data) > _MAX_RESTORE_BYTES:
                await interaction.followup.send("❌ Decompressed backup exceeds 256 MB limit.", ephemeral=True)
                return
        elif file.filename.endswith(".gz"):
            await interaction.edit_original_response(content="Decompressing backup…")
            data = await asyncio.to_thread(gzip.decompress, raw)
            if len(data) > _MAX_RESTORE_BYTES:
                await interaction.followup.send("❌ Decompressed backup exceeds 256 MB limit.", ephemeral=True)
                return
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
        view = RestoreConfirmView(data, interaction.user.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BackupCog(bot))
