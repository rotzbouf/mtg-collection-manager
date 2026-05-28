"""Shared UI helpers and constants used across multiple cogs."""
from __future__ import annotations

import discord

# ── Constants ────────────────────────────────────────────────────────────────

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


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_price(card: dict) -> str:
    parts = []
    if card.get("price_eur"):
        parts.append(f"€{card['price_eur']:.2f}")
    if card.get("price_usd"):
        parts.append(f"${card['price_usd']:.2f}")
    return " / ".join(parts) if parts else "—"


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


