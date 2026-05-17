"""Stats cog: /stats + OvercountView."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.auth import require_guest
from cogs.utils import LANG_EMOJI


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
        n = await interaction.client.db.move_cards_to_container(
            self._selected_entry_ids, self._selected_container_id
        )
        container = await interaction.client.db.get_container(self._selected_container_id)
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


class StatsView(discord.ui.View):
    """Attached to /stats — provides quick actions like viewing overcounted cards."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Overcounted Cards", emoji="⚠️", style=discord.ButtonStyle.secondary)
    async def btn_overcount(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        cards = await interaction.client.db.get_overcount_cards(threshold=4)
        if not cards:
            await interaction.followup.send(
                "No card appears more than 4 times in your collection.", ephemeral=True
            )
            return
        containers = await interaction.client.db.list_containers()
        embed = _overcount_summary_embed(cards)
        view = OvercountView(cards, containers)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="stats", description="Show collection statistics")
    async def cmd_stats(self, interaction: discord.Interaction):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(thinking=True)
        s = await interaction.client.db.stats()
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
                lang = c.get("language", "en")
                if lang != "en":
                    display = c.get("printed_name") or c.get("name_de") or c.get("name_en") or "?"
                    name_en = c.get("name_en") or ""
                    if display != name_en:
                        name = f"{display} ({name_en})"
                    else:
                        name = display
                else:
                    name = c.get("name_en") or "?"
                foil_tag = " ✨" if c.get("foil") else ""
                lang_flag = LANG_EMOJI.get(lang, "")
                container = c.get("container_name") or "—"
                lines.append(f"{i}. {name}{foil_tag} {lang_flag}  —  {_eur(c.get('price_eur'))}  📦 {container}")
            embed.add_field(name="Most valuable cards", value="\n".join(lines), inline=False)

        # ── Containers ──
        ct_stats = await interaction.client.db.container_stats()
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


async def setup(bot):
    await bot.add_cog(StatsCog(bot))
