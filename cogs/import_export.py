"""Import/export cog: /import /export."""
from __future__ import annotations

import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import core.exporter as exp
import core.importer as imp
from cogs.auth import require_guest, require_collector
from cogs.containers import container_autocomplete

logger = logging.getLogger(__name__)


async def _get_or_create_container(interaction: discord.Interaction, name: str) -> int:
    containers = await interaction.client.db.list_containers()
    match = next((c for c in containers if c["name"].lower() == name.lower()), None)
    if match:
        return match["id"]
    return await interaction.client.db.create_container(name)


class ImportConfirmView(discord.ui.View):
    def __init__(self, rows: list[dict], fmt: str, container_id: Optional[int], added_by: str):
        super().__init__(timeout=120)
        self._rows = rows
        self._fmt = fmt
        self._container_id = container_id
        self._added_by = added_by

    @discord.ui.button(label="Import", style=discord.ButtonStyle.success, emoji="📥")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.defer()

        if self._fmt == "moxfield_csv":
            total = sum(r["count"] for r in self._rows)
        else:
            total = len(self._rows)

        added = skipped = 0
        await interaction.edit_original_response(content=f"Importing… 0 / {total}", view=None)

        failed_names: list[str] = []
        if self._fmt == "moxfield_csv":
            for row in self._rows:
                card = None
                if row["collector_number"] and row["set_code"]:
                    card = await interaction.client.scryfall.get_by_collector(
                        row["set_code"], row["collector_number"], row["language"]
                    )
                    # Fallback: EN print (prices are there even for DE cards)
                    if not card and row["language"] != "en":
                        card = await interaction.client.scryfall.get_by_collector(
                            row["set_code"], row["collector_number"], "en"
                        )
                if not card:
                    card = await interaction.client.scryfall.get_by_name(row["name"], fuzzy=True, set_code=row["set_code"])
                if not card:
                    failed_names.append(row["name"])
                    skipped += row["count"]
                    continue
                card["condition"] = row["condition"]
                card["language"] = row["language"]
                card["foil"] = 1 if row["foil"] else 0
                card["container_id"] = self._container_id
                for _ in range(row["count"]):
                    await interaction.client.db.add_card(card, added_by=self._added_by)
                    added += 1
                if added % 10 == 0 or skipped:
                    await interaction.edit_original_response(
                        content=f"Importing… {added + skipped} / {total}"
                    )
        else:
            failed_reasons: list[str] = []
            for row in self._rows:
                try:
                    card, container_name = imp.normalize_row(row)
                    if container_name:
                        card["container_id"] = await _get_or_create_container(interaction, container_name)
                    await interaction.client.db.add_card(card, added_by=self._added_by)
                    added += 1
                except Exception as exc:
                    logger.warning("Import: skipped row — %s", exc)
                    failed_reasons.append(f"{row.get('name_en', '?')}: {exc}")
                    skipped += 1
                if (added + skipped) % 10 == 0:
                    await interaction.edit_original_response(
                        content=f"Importing… {added + skipped} / {total}"
                    )

        msg = f"Import complete — **{added}** card(s) added."
        if skipped:
            msg += f" **{skipped}** could not be resolved and were skipped."

        all_failures = failed_names if self._fmt == "moxfield_csv" else failed_reasons
        if all_failures:
            lines = "\n".join(f"• {f}" for f in all_failures[:10])
            if len(all_failures) > 10:
                lines += f"\n… and {len(all_failures) - 10} more"
            msg += f"\n\nFailed imports:\n{lines}"

        await interaction.edit_original_response(content=msg)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(content="Import cancelled.", view=None)


class ImportExportCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="export", description="Export your entire collection")
    @app_commands.describe(format="File format")
    @app_commands.choices(format=[
        app_commands.Choice(name="Moxfield CSV (recommended)", value="moxfield"),
        app_commands.Choice(name="CSV (Excel-compatible, full data)", value="csv"),
        app_commands.Choice(name="JSON", value="json"),
    ])
    async def cmd_export(self, interaction: discord.Interaction, format: str = "moxfield"):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(thinking=True)
        cards = await interaction.client.db.get_all()
        if not cards:
            await interaction.followup.send("Your collection is empty.", ephemeral=True)
            return

        if format == "moxfield":
            content = exp.to_moxfield(cards).encode("utf-8")
            filename = "collection_moxfield.csv"
        elif format == "json":
            content = exp.to_json(cards).encode("utf-8")
            filename = "collection.json"
        else:
            content = exp.to_csv(cards).encode("utf-8")
            filename = "collection.csv"

        file = discord.File(io.BytesIO(content), filename=filename)
        await interaction.followup.send(
            f"Exported **{len(cards)}** entries as `{filename}`.",
            file=file,
        )

    @app_commands.command(name="import", description="Import cards from a Moxfield CSV or bot export file")
    @app_commands.describe(
        file="Moxfield CSV, bot full CSV export, or bot JSON export",
        container="Assign all cards to this container (Moxfield CSV only; leave blank to keep original containers)",
    )
    @app_commands.autocomplete(container=container_autocomplete)
    async def cmd_import(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        container: Optional[str] = None,
    ):
        if not await require_collector(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        await interaction.edit_original_response(content="Reading file…")

        raw = await file.read()
        try:
            fmt = imp.detect_format(file.filename, raw)
        except ValueError as exc:
            await interaction.edit_original_response(content=str(exc))
            return

        await interaction.edit_original_response(content="Parsing…")
        try:
            if fmt == "moxfield_csv":
                rows = imp.parse_moxfield_csv(raw)
            elif fmt == "full_csv":
                rows = imp.parse_full_csv(raw)
            else:
                rows = imp.parse_json(raw)
        except Exception as exc:
            await interaction.edit_original_response(content=f"Could not parse file: {exc}")
            return

        if not rows:
            await interaction.edit_original_response(content="No cards found in the file.")
            return

        # Resolve target container (Moxfield CSV only)
        container_id: Optional[int] = None
        container_label = ""
        if container and fmt == "moxfield_csv":
            container_id = await _get_or_create_container(interaction, container)
            containers = await interaction.client.db.list_containers()
            c = next((c for c in containers if c["id"] == container_id), None)
            container_label = f" into 📦 **{c['name']}**" if c else ""

        total_cards = sum(r["count"] for r in rows) if fmt == "moxfield_csv" else len(rows)
        unique_rows = len(rows)

        lines = [
            f"**{unique_rows}** unique entr{'y' if unique_rows == 1 else 'ies'} → **{total_cards}** card(s) to import{container_label}",
            f"Format: `{fmt}`",
        ]
        if fmt == "moxfield_csv":
            lines.append("Each card will be looked up on Scryfall — large imports may take a moment.")

        view = ImportConfirmView(rows, fmt, container_id, interaction.user.display_name)
        await interaction.edit_original_response(content="\n".join(lines), view=view)


async def setup(bot):
    await bot.add_cog(ImportExportCog(bot))
