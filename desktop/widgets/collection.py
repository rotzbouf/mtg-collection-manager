"""Collection tab widget."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QComboBox, QPushButton, QLabel,
    QMessageBox, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer
from qasync import asyncSlot

from desktop.utils import display_name, lang_flag, format_price, SORT_OPTIONS
from desktop.widgets.card_detail import CardDetailPanel

PAGE_SIZE = 50

_COLUMNS = ["#", "Name", "Set", "CN", "Cond", "Foil", "Lang", "Container", "Price (EUR)"]


class CollectionWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = 0
        self._total = 0
        self._search_text = ""
        self._container_filter: Optional[int] = None
        self._language_filter: Optional[str] = None
        self._sort = "chaos"
        self._containers: list[dict] = []

        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._init_load)

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left pane ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar row 1: search + add button
        toolbar1 = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search cards…")
        self._search_edit.setClearButtonEnabled(True)
        self._add_btn = QPushButton("+ Add card")
        toolbar1.addWidget(self._search_edit)
        toolbar1.addWidget(self._add_btn)
        left_layout.addLayout(toolbar1)

        # Toolbar row 2: filters
        toolbar2 = QHBoxLayout()
        self._container_cb = QComboBox()
        self._container_cb.addItem("All containers", None)
        self._lang_cb = QComboBox()
        self._lang_cb.addItem("All languages", None)
        for code, label in [
            ("en", "English"), ("de", "German"), ("fr", "French"),
            ("it", "Italian"), ("es", "Spanish"), ("pt", "Portuguese"),
            ("ja", "Japanese"), ("ko", "Korean"), ("ru", "Russian"),
            ("zhs", "Simplified Chinese"), ("zht", "Traditional Chinese"),
        ]:
            self._lang_cb.addItem(label, code)

        self._sort_cb = QComboBox()
        for val, label in SORT_OPTIONS:
            self._sort_cb.addItem(label, val)

        toolbar2.addWidget(QLabel("Container:"))
        toolbar2.addWidget(self._container_cb)
        toolbar2.addWidget(QLabel("Language:"))
        toolbar2.addWidget(self._lang_cb)
        toolbar2.addWidget(QLabel("Sort:"))
        toolbar2.addWidget(self._sort_cb)
        left_layout.addLayout(toolbar2)

        # Table
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        left_layout.addWidget(self._table)

        # Pagination row
        pagination = QHBoxLayout()
        self._prev_btn = QPushButton("< Prev")
        self._next_btn = QPushButton("Next >")
        self._page_label = QLabel("Page 1 / 1 (0 cards)")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pagination.addWidget(self._prev_btn)
        pagination.addWidget(self._page_label, stretch=1)
        pagination.addWidget(self._next_btn)
        left_layout.addLayout(pagination)

        splitter.addWidget(left)

        # ---- Right pane: card detail ----
        self._detail = CardDetailPanel(show_buttons=True)
        self._detail.setMinimumWidth(280)
        self._detail.setMaximumWidth(340)
        splitter.addWidget(self._detail)
        splitter.setSizes([760, 300])

        root.addWidget(splitter)

        # ---- Search debounce timer ----
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(self._on_search_changed)

        # ---- Signal connections ----
        self._search_edit.textChanged.connect(lambda _: self._search_timer.start())
        self._container_cb.currentIndexChanged.connect(self._on_filter_changed)
        self._lang_cb.currentIndexChanged.connect(self._on_filter_changed)
        self._sort_cb.currentIndexChanged.connect(self._on_filter_changed)
        self._prev_btn.clicked.connect(self._on_prev)
        self._next_btn.clicked.connect(self._on_next)
        self._add_btn.clicked.connect(self._on_add_card)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._detail.edit_requested.connect(self._on_edit_card)
        self._detail.delete_requested.connect(self._on_delete_card)

    # ------------------------------------------------------------------ #
    # Initialisation                                                        #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _init_load(self):
        await self._reload_containers()
        await self._load_page()

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _reload_containers(self):
        from desktop.db import db
        self._containers = await db.list_containers()
        self._container_cb.blockSignals(True)
        self._container_cb.clear()
        self._container_cb.addItem("All containers", None)
        for c in self._containers:
            self._container_cb.addItem(c["name"], c["id"])
        self._container_cb.blockSignals(False)

    @asyncSlot()
    async def _load_page(self):
        from desktop.db import db

        query = self._search_text.strip()
        offset = self._page * PAGE_SIZE
        container_id = self._container_cb.currentData()
        language = self._lang_cb.currentData()
        sort = self._sort_cb.currentData() or "chaos"

        if query:
            cards = await db.search(query, limit=PAGE_SIZE, offset=offset)
            total = await db.count_search(query)
        else:
            cards = await db.list_cards(
                limit=PAGE_SIZE, offset=offset,
                sort=sort, language=language, container_id=container_id,
            )
            total = await db.count_cards(language=language, container_id=container_id)

        self._total = total
        self._populate_table(cards)
        self._update_pagination()

    def _populate_table(self, cards: list[dict]):
        self._table.setRowCount(0)
        self._cards = cards
        for row_idx, card in enumerate(cards):
            self._table.insertRow(row_idx)

            def _item(text: str, card_id: int | None = None) -> QTableWidgetItem:
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if card_id is not None:
                    item.setData(Qt.ItemDataRole.UserRole, card_id)
                return item

            self._table.setItem(row_idx, 0, _item(str(card.get("id", "")), card.get("id")))
            self._table.setItem(row_idx, 1, _item(display_name(card)))
            self._table.setItem(row_idx, 2, _item((card.get("set_code") or "").upper()))
            self._table.setItem(row_idx, 3, _item(card.get("collector_number") or ""))
            self._table.setItem(row_idx, 4, _item(card.get("condition") or ""))
            self._table.setItem(row_idx, 5, _item("★" if card.get("foil") else ""))
            self._table.setItem(row_idx, 6, _item(lang_flag(card)))
            self._table.setItem(row_idx, 7, _item(card.get("container_name") or ""))
            self._table.setItem(row_idx, 8, _item(format_price(card.get("price_eur"))))

    def _update_pagination(self):
        total_pages = max(1, (self._total + PAGE_SIZE - 1) // PAGE_SIZE)
        cur_page = self._page + 1
        self._page_label.setText(f"Page {cur_page} / {total_pages} ({self._total} cards)")
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page + 1 < total_pages)

    # ------------------------------------------------------------------ #
    # UI slots                                                              #
    # ------------------------------------------------------------------ #

    def _on_search_changed(self):
        self._search_text = self._search_edit.text()
        self._page = 0
        self._load_page()

    def _on_filter_changed(self):
        self._page = 0
        self._load_page()

    def _on_prev(self):
        if self._page > 0:
            self._page -= 1
            self._load_page()

    def _on_next(self):
        total_pages = max(1, (self._total + PAGE_SIZE - 1) // PAGE_SIZE)
        if self._page + 1 < total_pages:
            self._page += 1
            self._load_page()

    def _on_selection_changed(self):
        rows = self._table.selectedItems()
        if not rows:
            self._detail.clear()
            return
        row_idx = self._table.currentRow()
        id_item = self._table.item(row_idx, 0)
        if id_item is None:
            self._detail.clear()
            return
        card_id = id_item.data(Qt.ItemDataRole.UserRole)
        card = next((c for c in getattr(self, "_cards", []) if c.get("id") == card_id), None)
        if card:
            self._detail.set_card(card)
        else:
            self._detail.clear()

    def _on_add_card(self):
        from desktop.dialogs.add_card import AddCardDialog

        dlg = AddCardDialog(parent=self, containers=self._containers)
        dlg.card_confirmed.connect(self._do_add_card)
        dlg.exec()

    @asyncSlot(dict)
    async def _do_add_card(self, card: dict):
        from desktop.db import db

        try:
            await db.add_card(card, added_by="desktop")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not add card:\n{exc}")
            return
        await self._reload_containers()
        await self._load_page()

    def _on_edit_card(self, card: dict):
        from desktop.dialogs.edit_card import EditCardDialog

        dlg = EditCardDialog(card, containers=self._containers, parent=self)
        dlg.saved.connect(lambda changes: self._do_edit_card(card["id"], changes))
        dlg.exec()

    @asyncSlot()
    async def _do_edit_card(self, card_id: int, changes: dict):
        from desktop.db import db

        for field, value in changes.items():
            await db.update_card(card_id, field, value)
        await self._load_page()
        # Refresh detail panel
        updated = await db.get_card(card_id)
        if updated:
            self._detail.set_card(updated)

    def _on_delete_card(self, card: dict):
        name = display_name(card)
        reply = QMessageBox.question(
            self, "Delete card",
            f"Remove '{name}' from the collection?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_delete_card(card["id"])

    @asyncSlot()
    async def _do_delete_card(self, card_id: int):
        from desktop.db import db

        await db.remove_card(card_id)
        self._detail.clear()
        await self._load_page()

    # Public refresh hook called from MainWindow when DB changes externally
    @asyncSlot()
    async def refresh(self):
        await self._reload_containers()
        await self._load_page()
