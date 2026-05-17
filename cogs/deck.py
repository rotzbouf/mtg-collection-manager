"""Deck cog: /deck propose + DeckResultView etc."""
from __future__ import annotations

import io

import discord
from discord import app_commands
from discord.ext import commands

import core.deckbuilder as deckbuilder
from cogs.auth import require_guest


def _get_or_create_container_sync_helper(name: str):
    """Placeholder — the actual lookup happens via interaction.client."""
    pass


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
        containers = await interaction.client.db.list_containers()
        match = next((c for c in containers if c["name"].lower() == name.lower()), None)
        if match:
            container_id = match["id"]
        else:
            container_id = await interaction.client.db.create_container(name)
        n = await interaction.client.db.move_cards_to_container(self._card_ids, container_id)
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


class DeckCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    deck_group = app_commands.Group(name="deck", description="Build decks from your collection")

    @deck_group.command(name="propose", description="Generate a deck proposal from your collection")
    @app_commands.describe(format="Deck format")
    @app_commands.choices(format=[
        app_commands.Choice(name="Commander", value="commander"),
        app_commands.Choice(name="Timeless (Arena)", value="timeless"),
        app_commands.Choice(name="Standard", value="standard"),
    ])
    async def deck_propose(self, interaction: discord.Interaction, format: str = "commander"):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(thinking=True)
        pool = await interaction.client.db.get_all()

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


async def setup(bot):
    await bot.add_cog(DeckCog(bot))
