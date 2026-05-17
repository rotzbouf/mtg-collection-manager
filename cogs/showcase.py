"""Showcase cog: /showcase + ShowcaseView + WelcomeView."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import LANG_EMOJI, _fmt_price

logger = logging.getLogger(__name__)

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


async def _load_showcase_data(interaction: discord.Interaction, limit: int = 5) -> tuple[list[dict], list[list[dict]], list[Optional[bytes]]]:
    """Fetch top cards, their price histories, and pre-render charts."""
    cards = await interaction.client.db.get_top_by_value(limit)
    histories: list[list[dict]] = []
    charts: list[Optional[bytes]] = []
    for card in cards:
        history: list[dict] = []
        if card.get("scryfall_id"):
            history = await interaction.client.db.get_price_history(card["scryfall_id"])
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
            cards, histories, charts = await _load_showcase_data(interaction, 5)
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
        containers = await interaction.client.db.list_containers()
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
        s = await interaction.client.db.stats()
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


class ShowcaseCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="showcase", description="Show the 5 most valuable cards in your collection")
    async def cmd_showcase(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            cards, histories, charts = await _load_showcase_data(interaction, 5)
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


async def setup(bot):
    await bot.add_cog(ShowcaseCog(bot))
