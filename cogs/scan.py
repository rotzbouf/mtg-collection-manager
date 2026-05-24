"""Scan cog: on_message handler + all scan views/modals."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import struct
from pathlib import Path
from typing import NamedTuple, Optional

import discord
from discord.ext import commands

import core.scanner as scanner
from core.rate_limiter import RateLimiter
from core.scan_service import resolve_scan as _core_resolve_scan
from cogs.auth import require_collector
from cogs.utils import LANG_EMOJI, CONTAINER_TYPES, card_embed

logger = logging.getLogger(__name__)

SCAN_CHANNEL_ID     = int(os.getenv("DISCORD_SCAN_CHANNEL_ID",       0)) or None
SHOWCASE_CHANNEL_ID = int(os.getenv("DISCORD_SHOWCASE_CHANNEL_ID",    0)) or None
DEBUG_SCAN_PREVIEW  = os.getenv("DEBUG_SCAN_PREVIEW", "0") == "1"

_SCAN_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "heic"}
_MAX_SCAN_BYTES = scanner.MAX_INPUT_BYTES
_scan_limiter = RateLimiter(5, 60.0)  # 5 scans per 60 s per user

# Per-user last-used container: user_id -> (container_id, container_name)
# Bounded to prevent unbounded growth (I-3).
_MAX_LAST_CONTAINER = 500


class _LRUDict(dict):
    """dict that evicts the oldest entry when it exceeds _MAX_LAST_CONTAINER items."""
    def __setitem__(self, key, value):
        if key not in self and len(self) >= _MAX_LAST_CONTAINER:
            del self[next(iter(self))]
        super().__setitem__(key, value)


_last_container: _LRUDict = _LRUDict()


class _ScanMatch(NamedTuple):
    card: Optional[dict]
    detected_lang: str
    method_parts: list
    extracted_name: Optional[str]
    collector_info: dict


async def _resolve_scan(interaction_client, image_bytes: bytes) -> _ScanMatch:
    """Delegate to the shared scan_service pipeline."""
    card, detected_lang, method_parts, extracted_name, collector_info = (
        await _core_resolve_scan(interaction_client.scryfall, image_bytes)
    )
    return _ScanMatch(
        card=card,
        detected_lang=detected_lang,
        method_parts=method_parts,
        extracted_name=extracted_name,
        collector_info=collector_info,
    )


def _no_match_msg(m: _ScanMatch) -> str:
    """Human-readable reason why a scan produced no card match."""
    ci = m.collector_info
    if not scanner.ocr_available():
        return "OCR not available. Use `/add <name>` instead."
    if ci.get("set_code") and ci.get("collector_number"):
        return (
            f'Collector info read ({ci["set_code"]} #{ci["collector_number"]}) '
            f"but no Scryfall match. Enter the name manually."
        )
    if m.extracted_name:
        return f'Could not match **"{m.extracted_name}"** on Scryfall. Enter the name manually.'
    return "Could not read the card. Enter the name manually."


async def _do_scan_and_confirm(
    interaction: discord.Interaction,
    image_bytes: bytes,
    source_message: discord.Message,
    container_id: int,
    container_name: str,
):
    """OCR → Scryfall → show confirmation. Caller must not have responded yet (interaction-based)."""
    await interaction.response.defer(thinking=True)

    if DEBUG_SCAN_PREVIEW:
        preview = await asyncio.to_thread(scanner.get_isolated_preview, image_bytes)
        if preview:
            await interaction.followup.send(
                "🔍 **Debug preview** — isolated card + OCR name zone (red box):",
                file=discord.File(io.BytesIO(preview), filename="debug_preview.jpg"),
                ephemeral=True,
            )

    m = await _resolve_scan(interaction.client, image_bytes)
    logger.debug("OCR name: %r  footer: %s", m.extracted_name, m.collector_info)

    if DEBUG_SCAN_PREVIEW:
        ci = m.collector_info
        dbg = []
        if m.extracted_name:
            dbg.append(f"**OCR Name:** `{m.extracted_name}`")
        dbg.append(
            f"**Footer:** set=`{ci.get('set_code') or '—'}` "
            f"#=`{ci.get('collector_number') or '—'}` "
            f"lang=`{ci.get('language') or '—'}`"
        )
        await interaction.followup.send("🔍 **Debug — OCR results:**\n" + "\n".join(dbg), ephemeral=True)

    if not m.card:
        view = _ManualNameView(image_bytes, source_message, container_id, container_name)
        await interaction.followup.send(_no_match_msg(m), view=view, ephemeral=True)
        return

    card = m.card
    card["language"] = m.detected_lang or "en"
    card["container_id"] = container_id
    card["container_name"] = container_name
    match_method = "  •  ".join(m.method_parts)
    lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
    embed = card_embed(card, title_prefix="Found — confirm?  ")
    embed.description = (
        f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
        + (f"\n*{match_method}*" if match_method else "")
    )
    containers = await interaction.client.db.list_containers()
    view = ScanConfirmView(card, source_message, image_bytes, containers, match_method)
    await interaction.followup.send(embed=embed, view=view)


async def _do_scan_direct(
    bot,
    scanning_msg: discord.Message,
    image_bytes: bytes,
    source_message: discord.Message,
    container_id: int,
    container_name: str,
):
    """OCR → Scryfall → edit scanning_msg with confirmation (no interaction — used when container already known)."""
    try:
        m = await _resolve_scan(bot, image_bytes)
        logger.debug("OCR name: %r  footer: %s", m.extracted_name, m.collector_info)

        if not m.card:
            view = _ManualNameView(image_bytes, source_message, container_id, container_name)
            await scanning_msg.edit(content=_no_match_msg(m), view=view)
            return

        card = m.card
        card["language"] = m.detected_lang or "en"
        card["container_id"] = container_id
        card["container_name"] = container_name
        match_method = "  •  ".join(m.method_parts)
        lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
        embed = card_embed(card, title_prefix="Found — confirm?  ")
        embed.description = (
            f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
            + (f"\n*{match_method}*" if match_method else "")
        )
        containers = await bot.db.list_containers()
        view = ScanConfirmView(card, source_message, image_bytes, containers, match_method)
        await scanning_msg.edit(content="✅ Scanned — confirm below.")
        await scanning_msg.channel.send(embed=embed, view=view)
    except Exception as exc:
        logger.error("_do_scan_direct failed: %s", exc, exc_info=True)
        try:
            await scanning_msg.edit(content="⚠️ Scan failed — try again or use `/add`.", embed=None, view=None)
        except Exception:
            pass


class NewContainerModal(discord.ui.Modal, title="New container"):
    cont_name = discord.ui.TextInput(label="Container name", placeholder="e.g. Binder 1", max_length=100)
    cont_type = discord.ui.TextInput(
        label="Type  (binder / box / deck / trade / other)",
        placeholder="binder",
        required=False,
        max_length=20,
    )

    def __init__(self, image_bytes: bytes, source_message: discord.Message):
        super().__init__()
        self._image_bytes = image_bytes
        self._source = source_message

    async def on_submit(self, interaction: discord.Interaction):
        type_val = (self.cont_type.value or "binder").strip().lower()
        if type_val not in CONTAINER_TYPES:
            type_val = "binder"
        name = self.cont_name.value.strip()
        try:
            cid = await interaction.client.db.create_container(name, type=type_val)
        except Exception:
            containers = await interaction.client.db.list_containers()
            existing = next((c for c in containers if c["name"] == name), None)
            if not existing:
                await interaction.response.send_message("Could not create container.", ephemeral=True)
                return
            cid = existing["id"]
        _last_container[self._source.author.id] = (cid, name)
        await _do_scan_and_confirm(interaction, self._image_bytes, self._source, cid, name)


class ContainerSelectView(discord.ui.View):
    """Shown immediately when an image is posted. User picks or creates a container first."""

    def __init__(self, containers: list[dict], image_bytes: bytes, source_message: discord.Message):
        super().__init__(timeout=120)
        self._image_bytes = image_bytes
        self._source = source_message
        self._default = _last_container.get(source_message.author.id)  # (id, name) or None

        if containers:
            options = [
                discord.SelectOption(
                    label=c["name"],
                    value=str(c["id"]),
                    description=f"{c['type']} · {c['card_count']} cards",
                )
                for c in containers[:25]
            ]
            select = discord.ui.Select(placeholder="📦 Change container…", options=options, row=0)
            select.callback = self._on_select
            self.add_item(select)

        if self._default:
            use_btn = discord.ui.Button(
                label=f"Use: {self._default[1]}",
                style=discord.ButtonStyle.success,
                emoji="✅",
                row=1,
            )
            use_btn.callback = self._use_default
            self.add_item(use_btn)

        new_btn = discord.ui.Button(
            label="New container",
            style=discord.ButtonStyle.primary,
            emoji="➕",
            row=1,
        )
        new_btn.callback = self._new_container
        self.add_item(new_btn)

    async def _on_select(self, interaction: discord.Interaction):
        container_id = int(interaction.data["values"][0])
        c = await interaction.client.db.get_container(container_id)
        if not c:
            self.stop()
            await interaction.response.send_message(
                "This container was deleted. Please try again.", ephemeral=True
            )
            return
        _last_container[self._source.author.id] = (container_id, c["name"])
        self.stop()
        await _do_scan_and_confirm(interaction, self._image_bytes, self._source, container_id, c["name"])

    async def _use_default(self, interaction: discord.Interaction):
        self.stop()
        await _do_scan_and_confirm(
            interaction, self._image_bytes, self._source, self._default[0], self._default[1]
        )

    async def _new_container(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.send_modal(NewContainerModal(self._image_bytes, self._source))


class NameCorrectionModal(discord.ui.Modal, title="Correct card name"):
    card_name = discord.ui.TextInput(label="Card name", placeholder="e.g. Lightning Bolt", max_length=100)
    set_code = discord.ui.TextInput(label="Set code (optional)", placeholder="e.g. M10", required=False, max_length=10)

    def __init__(self, image_bytes: bytes, source_message: discord.Message, container_id: int, container_name: str):
        super().__init__()
        self._image_bytes = image_bytes
        self._source = source_message
        self._container_id = container_id
        self._container_name = container_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        card, detected_lang = await interaction.client.scryfall.resolve_card(
            self.card_name.value.strip(),
            self.set_code.value.strip() or None,
        )
        if not card:
            await interaction.followup.send(
                f'Card "{self.card_name.value}" not found on Scryfall.', ephemeral=True
            )
            return
        card["language"] = detected_lang or "en"
        card["container_id"] = self._container_id
        card["container_name"] = self._container_name
        lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
        embed = card_embed(card, title_prefix="Found — confirm?  ")
        embed.description = f"Language: {lang_flag}  |  Container: 📦 **{self._container_name}**"
        containers = await interaction.client.db.list_containers()
        view = ScanConfirmView(card, self._source, self._image_bytes, containers)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


class ScanConfirmView(discord.ui.View):
    def __init__(
        self,
        card: dict,
        source_message: discord.Message,
        image_bytes: bytes,
        containers: list[dict],
        match_method: str = "",
    ):
        super().__init__(timeout=120)
        self._card = card
        self._source = source_message
        self._image_bytes = image_bytes
        self._match_method = match_method

        # Row 1: container change select
        if containers:
            current_id = card.get("container_id")
            options = [
                discord.SelectOption(
                    label=c["name"][:100],
                    value=str(c["id"]),
                    description=f"{c.get('type', 'binder')} · {c['card_count']} cards",
                    default=(c["id"] == current_id),
                )
                for c in containers[:25]
            ]
            sel = discord.ui.Select(
                placeholder="📦 Change container for this & future scans…",
                options=options,
                row=1,
            )
            sel.callback = self._on_container
            self.add_item(sel)

        # Row 2: create a new container on the fly
        new_btn = discord.ui.Button(
            label="New container",
            style=discord.ButtonStyle.primary,
            emoji="➕",
            row=2,
        )
        new_btn.callback = self._new_container
        self.add_item(new_btn)

    def _build_embed(self) -> discord.Embed:
        lang_flag = LANG_EMOJI.get(self._card.get("language", "en"), "")
        container_name = self._card.get("container_name", "—")
        embed = card_embed(self._card, title_prefix="Found — confirm?  ")
        embed.description = (
            f"Language: {lang_flag}  |  Container: 📦 **{container_name}**"
            + (f"\n*{self._match_method}*" if self._match_method else "")
        )
        return embed

    async def _on_container(self, interaction: discord.Interaction):
        cid = int(interaction.data["values"][0])
        c = await interaction.client.db.get_container(cid)
        if not c:
            await interaction.response.send_message("Container no longer exists.", ephemeral=True)
            return
        self._card["container_id"] = cid
        self._card["container_name"] = c["name"]
        _last_container[self._source.author.id] = (cid, c["name"])
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def _new_container(self, interaction: discord.Interaction):
        await interaction.response.send_modal(NewContainerScanModal(self))

    async def _save(self, interaction: discord.Interaction, foil: bool):
        self._card["foil"] = foil
        self._card.setdefault("condition", "NM")
        self._card.setdefault("quantity", 1)
        try:
            row_id = await interaction.client.db.add_card(self._card, added_by=str(self._source.author.id))
        except Exception as exc:
            logger.error("_save add_card failed: %s", exc, exc_info=True)
            await interaction.response.send_message(
                "⚠️ Could not save card — check logs or try `/add`.", ephemeral=True
            )
            return
        self._card["id"] = row_id
        lang_flag = LANG_EMOJI.get(self._card.get("language", "en"), "")
        foil_tag = " ✨" if foil else ""
        embed = card_embed(self._card, title_prefix="Added ✅  ")
        embed.description = f"ID **{row_id}** | {lang_flag}{foil_tag} | added by {self._source.author.mention}"
        self.stop()
        self.clear_items()
        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as exc:
            logger.error("_save edit_message failed: %s", exc, exc_info=True)
            try:
                await interaction.followup.send(
                    f"✅ Saved **{self._card.get('name_en', '')}** (ID {row_id}) — could not update the message.",
                    ephemeral=True,
                )
            except Exception:
                pass

    @discord.ui.button(label="Add", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._save(interaction, foil=False)

    @discord.ui.button(label="Add as foil", style=discord.ButtonStyle.secondary, emoji="✨", row=0)
    async def add_foil(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._save(interaction, foil=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, emoji="✖", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        self.clear_items()
        await interaction.response.edit_message(content="Skipped.", embed=None, view=self)


class NewContainerScanModal(discord.ui.Modal, title="New container"):
    cont_name = discord.ui.TextInput(label="Container name", placeholder="e.g. Binder 2", max_length=100)
    cont_type = discord.ui.TextInput(
        label="Type  (binder / box / deck / trade / other)",
        placeholder="binder",
        required=False,
        max_length=20,
    )

    def __init__(self, confirm_view: ScanConfirmView):
        super().__init__()
        self._cv = confirm_view

    async def on_submit(self, interaction: discord.Interaction):
        type_val = (self.cont_type.value or "binder").strip().lower()
        if type_val not in CONTAINER_TYPES:
            type_val = "binder"
        name = self.cont_name.value.strip()
        try:
            cid = await interaction.client.db.create_container(name, type=type_val)
        except Exception:
            containers = await interaction.client.db.list_containers()
            existing = next((c for c in containers if c["name"] == name), None)
            if not existing:
                await interaction.response.send_message("Could not create container.", ephemeral=True)
                return
            cid = existing["id"]
        self._cv._card["container_id"] = cid
        self._cv._card["container_name"] = name
        _last_container[self._cv._source.author.id] = (cid, name)
        self._cv.stop()
        containers = await interaction.client.db.list_containers()
        new_view = ScanConfirmView(
            self._cv._card,
            self._cv._source,
            self._cv._image_bytes,
            containers,
            self._cv._match_method,
        )
        await interaction.response.edit_message(embed=new_view._build_embed(), view=new_view)


class _ManualNameView(discord.ui.View):
    def __init__(self, image_bytes: bytes, source_message: discord.Message, container_id: int, container_name: str):
        super().__init__(timeout=120)
        self._image_bytes = image_bytes
        self._source = source_message
        self._container_id = container_id
        self._container_name = container_name

    @discord.ui.button(label="Enter card name", style=discord.ButtonStyle.primary, emoji="🔍")
    async def enter_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(
            NameCorrectionModal(self._image_bytes, self._source, self._container_id, self._container_name)
        )


_BRIDGE_SOCK = str(Path(os.getenv("TMPDIR", "/tmp")) / "mtg_collection_bridge.sock")


async def _try_desktop_bridge(image_bytes: bytes, discord_user: str) -> dict | None:
    """Send scan to the desktop app over the Unix socket bridge.
    Returns result dict, or None if the desktop bridge is not running."""
    if not os.path.exists(_BRIDGE_SOCK):
        return None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(_BRIDGE_SOCK), timeout=3.0
        )
    except Exception:
        return None
    try:
        req = json.dumps({
            "image_b64": base64.b64encode(image_bytes).decode(),
            "discord_user": discord_user,
        }).encode()
        writer.write(struct.pack(">I", len(req)) + req)
        await writer.drain()

        # Wait up to 310 s for the user to confirm/skip in the desktop app
        raw_len = await asyncio.wait_for(reader.readexactly(4), timeout=310.0)
        length = struct.unpack(">I", raw_len)[0]
        raw = await asyncio.wait_for(reader.readexactly(length), timeout=5.0)
        return json.loads(raw.decode())
    except Exception as exc:
        logger.warning("Desktop bridge error: %s", exc)
        return None
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _post_bridge_result(
    bot,
    message: discord.Message,
    result: dict,
    scanning_msg: discord.Message,
) -> None:
    """Post the desktop scan result back to Discord."""
    status = result.get("status")
    if status == "skipped":
        await scanning_msg.edit(content="↩️ Scan declined in desktop app.")
        return
    if status == "error":
        await scanning_msg.edit(
            content=f"⚠️ Desktop scan error: {result.get('message', 'unknown')}"
        )
        return
    if status == "added":
        card = result.get("card", {})
        row_id = result.get("row_id", "?")
        container_name = result.get("container_name") or "—"
        foil = result.get("foil", False)
        from cogs.utils import card_embed, LANG_EMOJI
        embed = card_embed(card, title_prefix="Added via desktop ✅  ")
        lang = card.get("language", "en")
        foil_tag = " ✨" if foil else ""
        embed.description = (
            f"ID **{row_id}** | {LANG_EMOJI.get(lang, lang.upper())}{foil_tag} "
            f"| 📦 **{container_name}** | confirmed by {message.author.mention}"
        )
        await scanning_msg.edit(content="", embed=embed)
        return
    await scanning_msg.edit(content="⚠️ Unexpected response from desktop app.")


async def _handle_scan_attachment(bot, message: discord.Message, attachment: discord.Attachment):
    # Role check — only Collector or Admin may add cards via the scan channel (H-2).
    _collector_role_id = int(os.getenv("DISCORD_COLLECTOR_ROLE", 0) or 0)
    _admin_role_id     = int(os.getenv("DISCORD_ADMIN_ROLE",     0) or 0)
    if _collector_role_id or _admin_role_id:
        author_role_ids = {r.id for r in getattr(message.author, "roles", [])}
        if _collector_role_id not in author_role_ids and _admin_role_id not in author_role_ids:
            await message.add_reaction("🚫")
            return

    allowed, retry_after = _scan_limiter.check(message.author.id)
    if not allowed:
        await message.channel.send(
            f"{message.author.mention} ⏳ Too many scans — try again in {retry_after:.0f}s.",
            delete_after=10,
        )
        return
    if attachment.size > _MAX_SCAN_BYTES:
        await message.channel.send(
            f"{message.author.mention} ⚠️ Image too large (max {_MAX_SCAN_BYTES // (1024*1024)} MB).",
            delete_after=15,
        )
        return
    image_bytes = await attachment.read()

    # If the desktop app is running and has the bridge active, hand off to it
    bridge_result = await _try_desktop_bridge(
        image_bytes, f"@{message.author.display_name}"
    )
    if bridge_result is not None:
        scanning_msg = await message.channel.send(
            f"{message.author.mention} 🖥️ Processing in desktop app…"
        )
        await _post_bridge_result(bot, message, bridge_result, scanning_msg)
        return

    default = _last_container.get(message.author.id)

    if default:
        # Container already known — skip the selection step and scan immediately.
        container_id, container_name = default
        scanning_msg = await message.channel.send(
            f"{message.author.mention} 🔍 Scanning… 📦 **{container_name}**"
        )
        await _do_scan_direct(bot, scanning_msg, image_bytes, message, container_id, container_name)
    else:
        # No container known yet — ask first.
        containers = await bot.db.list_containers()
        view = ContainerSelectView(containers, image_bytes, message)
        await message.channel.send(
            f"{message.author.mention} 📦 Which container is this card going into?",
            view=view,
        )


class ScanCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Showcase channel: reply with the welcome menu whenever someone writes.
        if SHOWCASE_CHANNEL_ID and message.channel.id == SHOWCASE_CHANNEL_ID:
            from cogs.showcase import WelcomeView
            embed = discord.Embed(
                title="Welcome to the MTG Collection!",
                description=(
                    f"Hey {message.author.display_name}! "
                    "Here's what you can explore — all replies are private to you."
                ),
                color=0x5865f2,
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            await message.reply(embed=embed, view=WelcomeView(), mention_author=False)

        if SCAN_CHANNEL_ID is None or message.channel.id != SCAN_CHANNEL_ID:
            await self.bot.process_commands(message)
            return

        images = [
            a for a in message.attachments
            if (a.content_type or "").startswith("image/")
            or a.filename.lower().rsplit(".", 1)[-1] in _SCAN_IMAGE_EXTS
        ]
        if not images:
            await self.bot.process_commands(message)
            return

        for attachment in images:
            try:
                await _handle_scan_attachment(self.bot, message, attachment)
            except Exception as exc:
                logger.error("_handle_scan_attachment error: %s", exc, exc_info=True)
                try:
                    await message.channel.send(
                        f"{message.author.mention} ⚠️ Scan failed — try again or use `/add`.",
                        delete_after=30,
                    )
                except Exception:
                    pass


async def setup(bot):
    await bot.add_cog(ScanCog(bot))
