"""Container management cog: /container list + move."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from cogs.auth import require_guest, require_collector, require_admin
from cogs.utils import CONTAINER_TYPES


# ── Container autocomplete (shared, used by multiple cogs) ────────────────────

async def container_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    containers = await interaction.client.db.list_containers()
    return [
        app_commands.Choice(name=f"{c['name']} ({c['card_count']} cards)", value=str(c["id"]))
        for c in containers
        if current.lower() in c["name"].lower()
    ][:25]


# ── Views ─────────────────────────────────────────────────────────────────────

class _ContainerListView(discord.ui.View):
    """Attaches Browse and New Container buttons to the /container list response."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Browse", emoji="📦", style=discord.ButtonStyle.primary)
    async def browse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_guest(interaction):
            return
        containers = await interaction.client.db.list_containers()
        from cogs.collection import BrowseContainersView
        view = BrowseContainersView(containers)
        await interaction.response.send_message(
            "Select a container to browse:", view=view, ephemeral=True
        )

    @discord.ui.button(label="New Container", emoji="➕", style=discord.ButtonStyle.secondary)
    async def new_container(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        from cogs.collection import ContainerCreateModal
        await interaction.response.send_modal(ContainerCreateModal(refresh_browse=False))


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
        cards = await interaction.client.db.list_cards(limit=self._count + 100, container_id=self._src_id)
        card_ids = [c["id"] for c in cards]
        n = await interaction.client.db.move_cards_to_container(card_ids, self._dst_id)
        await interaction.response.edit_message(
            content=f"Moved **{n}** card(s) from 📦 **{self._src_name}** to 📦 **{self._dst_name}**.",
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


# ── Cog ───────────────────────────────────────────────────────────────────────

class ContainersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    container_group = app_commands.Group(
        name="container", description="Manage card containers (binders, boxes, …)"
    )

    @container_group.command(name="list", description="List all containers")
    async def container_list(self, interaction: discord.Interaction):
        if not await require_guest(interaction):
            return
        containers = await interaction.client.db.list_containers()
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
    async def container_move(self, interaction: discord.Interaction, source: str, destination: str):
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
        src = await interaction.client.db.get_container(src_id)
        dst = await interaction.client.db.get_container(dst_id)
        if not src or not dst:
            await interaction.response.send_message("Container not found.", ephemeral=True)
            return
        count = await interaction.client.db.count_cards(container_id=src_id)
        if count == 0:
            await interaction.response.send_message(f"📦 **{src['name']}** is empty.", ephemeral=True)
            return
        view = _ContainerMoveConfirmView(src_id, src["name"], dst_id, dst["name"], count)
        await interaction.response.send_message(
            f"Move **{count}** card(s) from 📦 **{src['name']}** → 📦 **{dst['name']}**?",
            view=view, ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(ContainersCog(bot))
