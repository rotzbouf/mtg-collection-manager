"""Shared UI helpers and constants used across multiple cogs."""
from __future__ import annotations

import discord

# ── Constants ────────────────────────────────────────────────────────────────

CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]

LANG_EMOJI = {
    "en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹",
    "es": "🇪🇸", "pt": "🇵🇹", "ja": "🇯🇵", "ko": "🇰🇷",
    "ru": "🇷🇺", "zhs": "🇨🇳", "zht": "🇹🇼",
}

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

CONTAINER_TYPES = ["binder", "box", "deck", "trade", "other"]

_SCAN_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "heic"}


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_price(card: dict) -> str:
    parts = []
    if card.get("price_eur"):
        parts.append(f"€{card['price_eur']:.2f}")
    if card.get("price_usd"):
        parts.append(f"${card['price_usd']:.2f}")
    return " / ".join(parts) if parts else "—"


def _fmt_set(card: dict) -> str:
    name = card.get("set_name") or "?"
    code = (card.get("set_code") or "?").upper()
    nr   = card.get("collector_number") or "?"
    return f"{name} ({code}) #{nr}"


def _fmt_condition(card: dict) -> str:
    cond = card.get("condition") or "NM"
    foil = " ✨" if card.get("foil") else ""
    lang = (card.get("language") or "en").upper()
    return f"{cond}{foil} · {lang}"


# ── Embed builders ────────────────────────────────────────────────────────────

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
    embed.add_field(name="Set", value=f"{card.get('set_name', '?')} ({(card.get('set_code') or '?').upper()})", inline=True)
    embed.add_field(name="№", value=card.get("collector_number") or "—", inline=True)
    embed.add_field(name="Rarity", value=rarity.capitalize(), inline=True)
    embed.add_field(name="Language", value=lang_flag, inline=True)

    cond = card.get("condition", "NM")
    foil = " ✨" if card.get("foil") else ""
    container = card.get("container_name") or "—"
    embed.add_field(name="Condition", value=f"{cond}{foil}", inline=True)
    embed.add_field(name="Container", value=container, inline=True)

    embed.add_field(name="Price", value=_fmt_price(card), inline=True)

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


def paginate_embeds(
    cards: list[dict], page: int, per_page: int = 10, total: int = 0
) -> tuple[discord.Embed, int]:
    """Build a paginated collection embed.

    When *total* is provided, *cards* is treated as the already-sliced page
    and total/pages are computed from the given true count.  Without it the
    function slices *cards* itself (legacy behaviour).
    """
    if total:
        pages = max(1, (total + per_page - 1) // per_page)
        chunk = cards
    else:
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
        embed.add_field(name=f"[{c.get('id', '?')}] {name}", value=val, inline=False)

    return embed, pages


# ── Nav / Select helpers ──────────────────────────────────────────────────────

def _nav_buttons(view: discord.ui.View, page: int, pages: int,
                 prev_cb, next_cb, *, row: int = 0) -> None:
    """Add ◀ [page/pages] ▶ buttons to a view."""
    if page > 1:
        btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=row)
        btn.callback = prev_cb
        view.add_item(btn)
    indicator = discord.ui.Button(
        label=f"{page} / {pages}", style=discord.ButtonStyle.secondary,
        disabled=True, row=row,
    )
    view.add_item(indicator)
    if page < pages:
        btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=row)
        btn.callback = next_cb
        view.add_item(btn)


def _card_select_label(c: dict) -> str:
    name = (c.get("printed_name") or c.get("name_en") or "Unknown")[:80]
    foil = " ✨" if c.get("foil") else ""
    lang = f" [{(c.get('language') or 'en').upper()}]" if c.get("language") != "en" else ""
    return f"{name}{foil}{lang}"[:100]


def _card_select_desc(c: dict) -> str:
    parts = [(c.get("set_code") or "?").upper(), c.get("condition", "NM")]
    if c.get("price_eur"):
        parts.append(f"€{c['price_eur']:.2f}")
    if c.get("container_name"):
        parts.append(f"📦 {c['container_name']}")
    return "  ·  ".join(parts)[:100]


def _add_card_select(view: discord.ui.View, cards: list[dict], row: int = 0) -> None:
    """Add a card picker Select to a view. Selecting opens the card manage panel."""
    if not cards:
        return
    options = [
        discord.SelectOption(
            label=_card_select_label(c),
            value=str(c["id"]),
            description=_card_select_desc(c),
        )
        for c in cards[:25]
    ]
    sel = discord.ui.Select(placeholder="Open card…", options=options, row=row)

    async def _on_card(interaction: discord.Interaction):
        from cogs.collection import CardManageView, _card_manage_embed
        card_id = int(interaction.data["values"][0])
        card = await interaction.client.db.get_card(card_id)
        if not card:
            await interaction.response.send_message("Card not found.", ephemeral=True)
            return
        await interaction.response.send_message(
            embed=_card_manage_embed(card),
            view=CardManageView(card, None, 0),
            ephemeral=True,
        )

    sel.callback = _on_card
    view.add_item(sel)


def _card_manage_embed(card: dict) -> discord.Embed:
    printed = card.get("printed_name") or card.get("name_en") or "Unknown"
    name_en = card.get("name_en") or ""
    title = printed if printed == name_en else f"{printed} ({name_en})"
    embed = discord.Embed(title=title, color=0xE74C3C)
    if card.get("image_url"):
        embed.set_thumbnail(url=card["image_url"])
    embed.add_field(name="Set", value=_fmt_set(card), inline=True)
    embed.add_field(name="Type", value=card.get("type_line") or "—", inline=True)
    embed.add_field(name="Condition", value=_fmt_condition(card), inline=True)
    embed.add_field(name="Price (EUR)", value=_fmt_price(card), inline=True)
    embed.add_field(name="Container", value=f"📦 {card.get('container_name') or '—'}", inline=True)
    if card.get("notes"):
        embed.add_field(name="Notes", value=card["notes"], inline=False)
    embed.set_footer(text=f"Collection ID: {card['id']}")
    return embed
