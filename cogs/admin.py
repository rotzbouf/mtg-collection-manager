"""Admin cog: /resync."""
from __future__ import annotations

import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.auth import require_admin, require_collector


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="resync", description="Re-fetch card data from Scryfall (admin only)")
    @app_commands.describe(id="Collection ID to resync (omit to resync all cards)")
    async def cmd_resync(self, interaction: discord.Interaction, id: Optional[int] = None):
        if not await require_admin(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)

        if id is not None:
            # Single card
            card_row = await interaction.client.db.get_card(id)
            if not card_row:
                await interaction.edit_original_response(content=f"No card with ID {id}.")
                return
            scryfall_id = card_row.get("scryfall_id")
            if not scryfall_id:
                await interaction.edit_original_response(content="Card has no Scryfall ID, cannot resync.")
                return
            await interaction.edit_original_response(content=f"Fetching **{card_row['name_en']}** from Scryfall...")
            fresh = await interaction.client.scryfall.get_by_id(scryfall_id)
            if not fresh:
                await interaction.edit_original_response(content="Scryfall returned no data for this card.")
                return
            rows = await interaction.client.db.resync_card(scryfall_id, fresh)
            await interaction.edit_original_response(
                content=f"Resynced **{fresh['name_en']}** — {rows} collection row(s) updated."
            )
            return

        # All cards
        scryfall_ids = await interaction.client.db.get_distinct_scryfall_ids()
        total = len(scryfall_ids)
        if not total:
            await interaction.edit_original_response(content="Collection is empty.")
            return

        await interaction.edit_original_response(content=f"Resyncing {total} unique cards from Scryfall...")
        _resync_sem = asyncio.Semaphore(10)

        async def _fetch_one(sid: str) -> tuple[str, Optional[dict]]:
            async with _resync_sem:
                return sid, await interaction.client.scryfall.get_by_id(sid)

        tasks = [asyncio.create_task(_fetch_one(sid)) for sid in scryfall_ids]
        updated_cards = 0
        failed = 0
        for i, task in enumerate(asyncio.as_completed(tasks), 1):
            sid, fresh = await task
            if fresh:
                await interaction.client.db.resync_card(sid, fresh)
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


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
