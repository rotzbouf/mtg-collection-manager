"""Collection cog: /add /list /search /browse + all browse/manage views."""
from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.auth import require_guest, require_collector, require_admin
from cogs.utils import (
    CONDITIONS, LANG_EMOJI, CONTAINER_TYPES,
    card_embed, paginate_embeds,
    _nav_buttons, _card_select_label, _card_select_desc, _add_card_select,
    _card_manage_embed,
)
from cogs.containers import container_autocomplete

_LIST_PER_PAGE   = 10
_SEARCH_PER_PAGE = 10
_BROWSE_PAGE_SIZE = 25


# ── /add helper views ─────────────────────────────────────────────────────────

class _AddAnotherView(discord.ui.View):
    """Shown after /add — lets the user add one more copy of the same card."""
    def __init__(self, card: dict, added_by: str, count: int, base_desc: str):
        super().__init__(timeout=120)
        self._card = card
        self._added_by = added_by
        self._count = count
        self._base_desc = base_desc

    @discord.ui.button(label="➕ Add Another Copy", style=discord.ButtonStyle.success)
    async def add_another(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        new_id = await interaction.client.db.add_card(self._card, added_by=self._added_by)
        self._count += 1
        embed = card_embed(self._card, title_prefix="Added ✅  ")
        n = self._count - 1
        embed.description = self._base_desc + f"\n➕ +{n} additional cop{'y' if n == 1 else 'ies'} added (last ID: **{new_id}**)"
        await interaction.response.edit_message(embed=embed, view=self)


# ── Paginated list / search views ─────────────────────────────────────────────

class ListPageView(discord.ui.View):
    def __init__(self, page: int, pages: int, total: int,
                 sort: str, language: str, cards: list[dict]):
        super().__init__(timeout=300)
        self._page = page
        self._pages = pages
        self._total = total
        self._sort = sort
        self._language = language
        _add_card_select(self, cards, row=0)
        _nav_buttons(self, page, pages, self._prev, self._next, row=1)

    async def _go(self, interaction: discord.Interaction, page: int):
        cards = await interaction.client.db.list_cards(
            limit=_LIST_PER_PAGE,
            offset=(page - 1) * _LIST_PER_PAGE,
            sort=self._sort,
            language=self._language or None,
        )
        embed, _ = paginate_embeds(cards, page, per_page=_LIST_PER_PAGE, total=self._total)
        view = ListPageView(page, self._pages, self._total, self._sort, self._language, cards)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev(self, interaction: discord.Interaction):
        await self._go(interaction, self._page - 1)

    async def _next(self, interaction: discord.Interaction):
        await self._go(interaction, self._page + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class SearchPageView(discord.ui.View):
    def __init__(self, query: str, page: int, pages: int, total: int, cards: list[dict]):
        super().__init__(timeout=300)
        self._query = query
        self._page = page
        self._pages = pages
        self._total = total
        self._cache: dict[int, list[dict]] = {page: cards}
        _add_card_select(self, cards, row=0)
        _nav_buttons(self, page, pages, self._prev, self._next, row=1)

    async def _go(self, interaction: discord.Interaction, page: int):
        if page not in self._cache:
            results = await interaction.client.db.search(
                self._query, limit=_SEARCH_PER_PAGE,
                offset=(page - 1) * _SEARCH_PER_PAGE,
            )
            self._cache[page] = results
        else:
            results = self._cache[page]
        embed, _ = paginate_embeds(results, page, per_page=_SEARCH_PER_PAGE, total=self._total)
        embed.title = f'Search: "{self._query}"  —  {self._total} result(s)'
        view = SearchPageView(self._query, page, self._pages, self._total, results)
        view._cache = self._cache  # forward the cache to the new view
        await interaction.response.edit_message(embed=embed, view=view)

    async def _prev(self, interaction: discord.Interaction):
        await self._go(interaction, self._page - 1)

    async def _next(self, interaction: discord.Interaction):
        await self._go(interaction, self._page + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ── Browse views ──────────────────────────────────────────────────────────────

class ContainerCreateModal(discord.ui.Modal, title="Create container"):
    cont_name = discord.ui.TextInput(label="Container name", placeholder="e.g. Binder 1", max_length=100)
    cont_type = discord.ui.TextInput(
        label="Type  (binder / box / deck / trade / other)",
        placeholder="binder",
        required=False,
        max_length=20,
    )
    cont_desc = discord.ui.TextInput(
        label="Description (optional)",
        placeholder="e.g. Blue cards from 2023",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, refresh_browse: bool = True):
        super().__init__()
        self._refresh_browse = refresh_browse

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_collector(interaction):
            return
        type_val = (self.cont_type.value or "binder").strip().lower()
        if type_val not in CONTAINER_TYPES:
            type_val = "binder"
        name = self.cont_name.value.strip()
        desc = self.cont_desc.value.strip() or ""
        try:
            await interaction.client.db.create_container(name, desc, type_val)
        except Exception:
            await interaction.response.send_message(
                f'A container named **{name}** already exists.', ephemeral=True
            )
            return
        if self._refresh_browse:
            containers = await interaction.client.db.list_containers()
            view = BrowseContainersView(containers)
            await interaction.response.edit_message(
                content="Select a container to browse:", embed=None, view=view
            )
        else:
            await interaction.response.send_message(
                f'📦 Container **{name}** (`{type_val}`) created.', ephemeral=True
            )


class ContainerRenameModal(discord.ui.Modal, title="Rename container"):
    new_name = discord.ui.TextInput(label="New name", max_length=100)

    def __init__(self, container: dict, page: int, total: int):
        super().__init__()
        self._container = container
        self._page = page
        self._total = total
        self.new_name.default = container["name"]

    async def on_submit(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        name = self.new_name.value.strip()
        ok = await interaction.client.db.rename_container(self._container["id"], name)
        if not ok:
            await interaction.response.send_message("Could not rename container.", ephemeral=True)
            return
        self._container["name"] = name
        cards = await interaction.client.db.list_cards(
            limit=_BROWSE_PAGE_SIZE, offset=self._page * _BROWSE_PAGE_SIZE,
            container_id=self._container["id"],
        )
        view = BrowseCardsView(self._container, cards, self._total, self._page)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)


class _BrowseContainerDeleteConfirmView(discord.ui.View):
    def __init__(self, container: dict):
        super().__init__(timeout=30)
        self._container = container

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        if not await require_admin(interaction):
            return
        await interaction.client.db.delete_container(self._container["id"])
        containers = await interaction.client.db.list_containers()
        view = BrowseContainersView(containers)
        await interaction.response.edit_message(
            content=f'Container **{self._container["name"]}** deleted. Cards were kept.',
            embed=None, view=view,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        cards = await interaction.client.db.list_cards(
            limit=_BROWSE_PAGE_SIZE, offset=0, container_id=self._container["id"]
        )
        total = await interaction.client.db.count_cards(container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, total, page=0)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)


class BrowseContainersView(discord.ui.View):
    def __init__(self, containers: list[dict]):
        super().__init__(timeout=300)
        if containers:
            options = [
                discord.SelectOption(
                    label=c["name"][:100],
                    value=str(c["id"]),
                    description=(
                        f"{c.get('type', 'binder')} · {c['card_count']} cards"
                        + (f" · €{c['total_value_eur']:.2f}" if c.get("total_value_eur") else "")
                    )[:100],
                    emoji="📦",
                )
                for c in containers[:25]
            ]
            sel = discord.ui.Select(placeholder="Select a container…", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        create_btn = discord.ui.Button(
            label="New Container", emoji="➕", style=discord.ButtonStyle.primary, row=1
        )
        create_btn.callback = self._create
        self.add_item(create_btn)

    async def _on_select(self, interaction: discord.Interaction):
        container_id = int(interaction.data["values"][0])
        container = await interaction.client.db.get_container(container_id)
        if not container:
            await interaction.response.edit_message(content="Container no longer exists.", embed=None, view=None)
            return
        total = await interaction.client.db.count_cards(container_id=container_id)
        cards = await interaction.client.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=0, container_id=container_id)
        view = BrowseCardsView(container, cards, total, page=0)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)

    async def _create(self, interaction: discord.Interaction):
        if not await require_collector(interaction):
            return
        await interaction.response.send_modal(ContainerCreateModal(refresh_browse=True))

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class BrowseCardsView(discord.ui.View):
    def __init__(self, container: dict, cards: list[dict], total: int, page: int):
        super().__init__(timeout=300)
        self._container = container
        self._total = total
        self._page = page
        self._pages = max(1, (total + _BROWSE_PAGE_SIZE - 1) // _BROWSE_PAGE_SIZE)

        if cards:
            options = [
                discord.SelectOption(
                    label=self._label(c),
                    value=str(c["id"]),
                    description=self._desc(c),
                )
                for c in cards
            ]
            sel = discord.ui.Select(
                placeholder=f"Select a card… ({total} in container)",
                options=options,
                row=0,
            )
            sel.callback = self._on_card
            self.add_item(sel)
        else:
            empty = discord.ui.Button(label="(empty container)", style=discord.ButtonStyle.secondary, disabled=True, row=0)
            self.add_item(empty)

        back_btn = discord.ui.Button(label="◀ Containers", style=discord.ButtonStyle.secondary, row=1)
        back_btn.callback = self._back
        self.add_item(back_btn)

        if page > 0:
            prev_btn = discord.ui.Button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)

        if (page + 1) * _BROWSE_PAGE_SIZE < total:
            next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

        rename_btn = discord.ui.Button(label="Rename", emoji="✏️", style=discord.ButtonStyle.secondary, row=2)
        rename_btn.callback = self._rename
        self.add_item(rename_btn)

        delete_btn = discord.ui.Button(label="Delete Container", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
        delete_btn.callback = self._delete
        self.add_item(delete_btn)

    @staticmethod
    def _label(c: dict) -> str:
        return _card_select_label(c)

    @staticmethod
    def _desc(c: dict) -> str:
        parts = [(c.get("set_code") or "?").upper(), c.get("condition", "NM")]
        if c.get("price_eur"):
            parts.append(f"€{c['price_eur']:.2f}")
        return "  ·  ".join(parts)[:100]

    def make_embed(self) -> discord.Embed:
        c = self._container
        embed = discord.Embed(
            title=f"📦 {c['name']}",
            description=f"{c.get('type', 'binder').capitalize()} · {self._total} card(s)",
            color=0x5865F2,
        )
        if self._pages > 1:
            embed.set_footer(text=f"Page {self._page + 1} / {self._pages}")
        return embed

    async def _on_card(self, interaction: discord.Interaction):
        card_id = int(interaction.data["values"][0])
        card = await interaction.client.db.get_card(card_id)
        if not card:
            await interaction.response.edit_message(content="Card not found.", embed=None, view=None)
            return
        view = CardManageView(card, self._container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(card), view=view)

    async def _back(self, interaction: discord.Interaction):
        containers = await interaction.client.db.list_containers()
        view = BrowseContainersView(containers)
        await interaction.response.edit_message(content="Select a container to browse:", embed=None, view=view)

    async def _prev(self, interaction: discord.Interaction):
        page = self._page - 1
        cards = await interaction.client.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, self._total, page)
        await interaction.response.edit_message(embed=view.make_embed(), view=view)

    async def _next(self, interaction: discord.Interaction):
        page = self._page + 1
        cards = await interaction.client.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, self._total, page)
        await interaction.response.edit_message(embed=view.make_embed(), view=view)

    async def _rename(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        await interaction.response.send_modal(
            ContainerRenameModal(self._container, self._page, self._total)
        )

    async def _delete(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        name = self._container["name"]
        view = _BrowseContainerDeleteConfirmView(self._container)
        await interaction.response.edit_message(
            content=f'Delete container **{name}**? Cards in it will not be deleted.',
            embed=None, view=view,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


class CardManageView(discord.ui.View):
    def __init__(self, card: dict, container: Optional[dict], page: int):
        super().__init__(timeout=300)
        self._card = card
        self._container = container
        self._page = page
        if container is None:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.label == "◀ Back":
                    item.label = "✕ Close"
                    break

    @discord.ui.button(label="Move", style=discord.ButtonStyle.primary, emoji="📦", row=0)
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        containers = await interaction.client.db.list_containers()
        others = [c for c in containers if c["id"] != self._card.get("container_id")]
        if not others:
            await interaction.response.send_message("No other containers available.", ephemeral=True)
            return
        view = MoveCardView(self._card, others, self._container, self._page)
        await interaction.response.edit_message(content="Select destination container:", embed=None, view=view)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.secondary, emoji="✏️", row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        await interaction.response.send_modal(EditCardModal(self._card, self._container, self._page))

    @discord.ui.button(label="Resync", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def resync(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_collector(interaction):
            return
        scryfall_id = self._card.get("scryfall_id")
        if not scryfall_id:
            await interaction.response.send_message("Card has no Scryfall ID.", ephemeral=True)
            return
        await interaction.response.defer()
        fresh = await interaction.client.scryfall.get_by_id(scryfall_id)
        if not fresh:
            await interaction.followup.send("Scryfall returned no data for this card.", ephemeral=True)
            return
        await interaction.client.db.resync_card(scryfall_id, fresh)
        card = await interaction.client.db.get_card(self._card["id"])
        self._card = card
        await interaction.edit_original_response(embed=_card_manage_embed(card), view=self)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await require_admin(interaction):
            return
        view = _BrowseDeleteConfirmView(self._card, self._container, self._page)
        name = self._card.get("name_en") or "this card"
        await interaction.response.edit_message(
            content=f"Delete **{name}** (ID {self._card['id']}) from the collection?",
            embed=None, view=view,
        )

    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._container is None:
            await interaction.response.edit_message(content="✕", embed=None, view=None)
            return
        total = await interaction.client.db.count_cards(container_id=self._container["id"])
        page = min(self._page, max(0, (total - 1) // _BROWSE_PAGE_SIZE)) if total else 0
        cards = await interaction.client.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, total, page)
        await interaction.response.edit_message(content=None, embed=view.make_embed(), view=view)


class MoveCardView(discord.ui.View):
    def __init__(self, card: dict, containers: list[dict], current_container: Optional[dict], page: int):
        super().__init__(timeout=300)
        self._card = card
        self._current_container = current_container
        self._page = page
        options = [
            discord.SelectOption(
                label=c["name"][:100],
                value=str(c["id"]),
                description=f"{c.get('type', 'binder')} · {c['card_count']} cards"[:100],
                emoji="📦",
            )
            for c in containers[:25]
        ]
        sel = discord.ui.Select(placeholder="Move to…", options=options, row=0)
        sel.callback = self._on_select
        self.add_item(sel)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _on_select(self, interaction: discord.Interaction):
        new_id = int(interaction.data["values"][0])
        await interaction.client.db.update_card(self._card["id"], "container_id", new_id)
        card = await interaction.client.db.get_card(self._card["id"])
        view = CardManageView(card, self._current_container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(card), view=view)

    async def _cancel(self, interaction: discord.Interaction):
        view = CardManageView(self._card, self._current_container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(self._card), view=view)


class _BrowseDeleteConfirmView(discord.ui.View):
    def __init__(self, card: dict, container: Optional[dict], page: int):
        super().__init__(timeout=60)
        self._card = card
        self._container = container
        self._page = page

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        name = self._card.get("name_en") or "Card"
        await interaction.client.db.remove_card(self._card["id"])
        if self._container is None:
            await interaction.response.edit_message(content=f"**{name}** removed.", embed=None, view=None)
            return
        total = await interaction.client.db.count_cards(container_id=self._container["id"])
        page = min(self._page, max(0, (total - 1) // _BROWSE_PAGE_SIZE)) if total else 0
        cards = await interaction.client.db.list_cards(limit=_BROWSE_PAGE_SIZE, offset=page * _BROWSE_PAGE_SIZE, container_id=self._container["id"])
        view = BrowseCardsView(self._container, cards, total, page)
        await interaction.response.edit_message(content=f"Deleted **{name}**.", embed=view.make_embed(), view=view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = CardManageView(self._card, self._container, self._page)
        await interaction.response.edit_message(content=None, embed=_card_manage_embed(self._card), view=view)


class EditCardModal(discord.ui.Modal, title="Edit card"):
    def __init__(self, card: dict, container: Optional[dict], page: int):
        super().__init__()
        self._card = card
        self._container = container
        self._page = page
        self._condition_input = discord.ui.TextInput(
            label="Condition",
            placeholder="NM / LP / MP / HP / DMG",
            default=card.get("condition") or "NM",
            required=False,
            max_length=3,
        )
        self._language_input = discord.ui.TextInput(
            label="Language",
            placeholder="en / de",
            default=card.get("language") or "en",
            required=False,
            max_length=5,
        )
        self._foil_input = discord.ui.TextInput(
            label="Foil (0 = no, 1 = yes)",
            placeholder="0 or 1",
            default="1" if card.get("foil") else "0",
            required=False,
            max_length=1,
        )
        self._notes_input = discord.ui.TextInput(
            label="Notes",
            placeholder="Free-text notes…",
            default=card.get("notes") or "",
            required=False,
            max_length=200,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self._condition_input)
        self.add_item(self._language_input)
        self.add_item(self._foil_input)
        self.add_item(self._notes_input)

    async def on_submit(self, interaction: discord.Interaction):
        card_id = self._card["id"]
        cond = self._condition_input.value.strip().upper()
        if cond in ("NM", "LP", "MP", "HP", "DMG"):
            await interaction.client.db.update_card(card_id, "condition", cond)
        lang = self._language_input.value.strip().lower()
        if lang in ("en", "de"):
            await interaction.client.db.update_card(card_id, "language", lang)
        foil_val = self._foil_input.value.strip()
        if foil_val in ("0", "1"):
            await interaction.client.db.update_card(card_id, "foil", int(foil_val))
        notes = self._notes_input.value.strip() or None
        await interaction.client.db.update_card(card_id, "notes", notes)
        card = await interaction.client.db.get_card(card_id)
        view = CardManageView(card, self._container, self._page)
        await interaction.response.edit_message(embed=_card_manage_embed(card), view=view)


# ── Cog ───────────────────────────────────────────────────────────────────────

class CollectionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="add", description="Add a card to your collection by name")
    @app_commands.describe(
        name="Card name (English or German)",
        container="Container to put the card in",
        set_code="Set code, e.g. 'MH3' (optional)",
        language="Override detected language (en/de)",
        condition="Card condition: NM, LP, MP, HP, DMG",
        foil="Is this a foil?",
        quantity="How many copies",
        notes="Personal notes",
    )
    @app_commands.autocomplete(container=container_autocomplete)
    @app_commands.choices(condition=[app_commands.Choice(name=c, value=c) for c in CONDITIONS])
    @app_commands.choices(language=[
        app_commands.Choice(name="English", value="en"),
        app_commands.Choice(name="German / Deutsch", value="de"),
    ])
    async def cmd_add(
        self,
        interaction: discord.Interaction,
        name: str,
        container: str = "",
        set_code: str = "",
        language: str = "",
        condition: str = "NM",
        foil: bool = False,
        quantity: int = 1,
        notes: str = "",
    ):
        if not await require_collector(interaction):
            return
        await interaction.response.defer(thinking=True)

        card, detected_lang = await interaction.client.scryfall.resolve_card(name, set_code or None)
        if not card:
            await interaction.followup.send(f"Card **{name}** not found on Scryfall.", ephemeral=True)
            return

        card["language"] = language or detected_lang or "en"
        card["condition"] = condition
        card["foil"] = foil
        card["quantity"] = 1
        card["notes"] = notes or None

        # Resolve container
        container_id = None
        container_name = None
        if container:
            if container.isdigit():
                container_id = int(container)
                c = await interaction.client.db.get_container(container_id)
                if not c:
                    await interaction.followup.send(
                        f"No container with ID **{container}** found. Use `/container list` to see available containers.",
                        ephemeral=True,
                    )
                    return
                container_name = c["name"]
            else:
                existing = await interaction.client.db.list_containers()
                match = next((c for c in existing if c["name"].lower() == container.lower()), None)
                if match:
                    container_id = match["id"]
                    container_name = match["name"]
                else:
                    container_id = await interaction.client.db.create_container(container)
                    container_name = container
        card["container_id"] = container_id
        card["container_name"] = container_name

        copies = max(1, quantity)
        ids = []
        for _ in range(copies):
            ids.append(await interaction.client.db.add_card(card, added_by=str(interaction.user.id)))

        card["id"] = ids[0]
        lang_flag = LANG_EMOJI.get(card["language"], card["language"].upper())
        new_tag = "  *(container created)*" if container and not container.isdigit() and container_name == container else ""
        id_range = f"IDs **{ids[0]}–{ids[-1]}**" if len(ids) > 1 else f"ID **{ids[0]}**"
        desc = f"Saved as {id_range} ({len(ids)} cop{'y' if len(ids)==1 else 'ies'}) | Language {lang_flag}{new_tag}"
        embed = card_embed(card, title_prefix="Added ✅  ")
        embed.description = desc
        view = _AddAnotherView(card, str(interaction.user.id), len(ids), desc)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="search", description="Full-text search across all card fields")
    @app_commands.describe(query="Search terms (name, type, oracle text, set, …)")
    async def cmd_search(self, interaction: discord.Interaction, query: str):
        if not await require_guest(interaction):
            return
        query = query.strip()
        if not query:
            await interaction.response.send_message("Please enter a search term.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        total = await interaction.client.db.count_search(query)
        if not total:
            await interaction.followup.send(f"No results for **{query}**.", ephemeral=True)
            return
        pages = max(1, (total + _SEARCH_PER_PAGE - 1) // _SEARCH_PER_PAGE)
        results = await interaction.client.db.search(query, limit=_SEARCH_PER_PAGE, offset=0)
        embed, _ = paginate_embeds(results, 1, per_page=_SEARCH_PER_PAGE, total=total)
        embed.title = f'Search: "{query}"  —  {total} result(s)'
        view = SearchPageView(query, 1, pages, total, results)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="list", description="List your collection")
    @app_commands.describe(
        page="Page number",
        sort="Sort order",
        language="Filter by language",
    )
    @app_commands.choices(sort=[
        app_commands.Choice(name="Chaos (default)", value="chaos"),
        app_commands.Choice(name="Name A-Z", value="name"),
        app_commands.Choice(name="Set + collector №", value="set"),
        app_commands.Choice(name="CMC", value="cmc"),
        app_commands.Choice(name="Recently added", value="added"),
    ])
    @app_commands.choices(language=[
        app_commands.Choice(name="All", value=""),
        app_commands.Choice(name="English", value="en"),
        app_commands.Choice(name="German / Deutsch", value="de"),
    ])
    async def cmd_list(
        self,
        interaction: discord.Interaction,
        page: int = 1,
        sort: str = "chaos",
        language: str = "",
    ):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(thinking=True)
        total = await interaction.client.db.count_cards(language=language or None)
        if not total:
            await interaction.followup.send("Your collection is empty.", ephemeral=True)
            return
        pages = max(1, (total + _LIST_PER_PAGE - 1) // _LIST_PER_PAGE)
        page = max(1, min(page, pages))
        cards = await interaction.client.db.list_cards(
            limit=_LIST_PER_PAGE,
            offset=(page - 1) * _LIST_PER_PAGE,
            sort=sort,
            language=language or None,
        )
        embed, _ = paginate_embeds(cards, page, per_page=_LIST_PER_PAGE, total=total)
        view = ListPageView(page, pages, total, sort, language, cards)
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="browse", description="Browse containers and manage cards interactively")
    async def cmd_browse(self, interaction: discord.Interaction):
        if not await require_guest(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        containers = await interaction.client.db.list_containers()
        if not containers:
            view = BrowseContainersView(containers)
            await interaction.edit_original_response(
                content="No containers yet. Create your first one:", view=view
            )
            return
        view = BrowseContainersView(containers)
        await interaction.edit_original_response(content="Select a container to browse:", view=view)


async def setup(bot):
    await bot.add_cog(CollectionCog(bot))
