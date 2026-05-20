"""Collection tab widget."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QPushButton, QLabel, QComboBox,
    QMessageBox, QAbstractItemView, QMenu, QDialog,
    QApplication, QFrame,
)
from PyQt6.QtCore import Qt, QTimer
from qasync import asyncSlot

from desktop.utils import display_name, lang_flag, format_price
from desktop.widgets.card_detail import CardDetailPanel

PAGE_SIZE = 50

_COLUMNS = ["#", "Name", "Set", "CN", "Cond", "Foil", "Lang", "Container", "Price (EUR)"]


class CollectionWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = 0
        self._total = 0
        self._search_text = ""
        self._id_text = ""
        self._no_container_mode = False
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
        left_layout.setSpacing(4)

        # Toolbar: search + filters
        toolbar = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search cards…")
        self._search_edit.setClearButtonEnabled(True)
        toolbar.addWidget(self._search_edit, stretch=1)
        toolbar.addWidget(QLabel("ID:"))
        self._id_edit = QLineEdit()
        self._id_edit.setPlaceholderText("e.g. 42")
        self._id_edit.setClearButtonEnabled(True)
        self._id_edit.setFixedWidth(90)
        toolbar.addWidget(self._id_edit)
        self._no_container_btn = QPushButton("🗂 No container")
        self._no_container_btn.setCheckable(True)
        self._no_container_btn.setToolTip("Show only cards not assigned to any container")
        self._no_container_btn.setFixedWidth(130)
        toolbar.addWidget(self._no_container_btn)
        left_layout.addLayout(toolbar)

        # Table
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in [0, 2, 3, 4, 5, 6, 7, 8]:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        left_layout.addWidget(self._table)

        # Pagination
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
        self._detail.setMinimumWidth(360)
        self._detail.setMaximumWidth(460)
        splitter.addWidget(self._detail)
        splitter.setSizes([680, 400])

        root.addWidget(splitter)

        # ---- Search debounce ----
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(self._on_search_changed)

        self._id_timer = QTimer()
        self._id_timer.setSingleShot(True)
        self._id_timer.setInterval(350)
        self._id_timer.timeout.connect(self._on_id_changed)

        # ---- Signal connections ----
        self._search_edit.textChanged.connect(lambda _: self._search_timer.start())
        self._id_edit.textChanged.connect(lambda _: self._id_timer.start())
        self._prev_btn.clicked.connect(self._on_prev)
        self._next_btn.clicked.connect(self._on_next)
        self._no_container_btn.toggled.connect(self._on_no_container_toggled)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.customContextMenuRequested.connect(self._on_card_context_menu)
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

    @asyncSlot()
    async def _load_page(self):
        from desktop.db import db

        id_text = self._id_text.strip()
        if id_text:
            try:
                card_id = int(id_text)
            except ValueError:
                self._total = 0
                self._populate_table([])
                self._update_pagination()
                return
            card = await db.get_card(card_id)
            cards = [card] if card else []
            self._total = len(cards)
            self._populate_table(cards)
            self._update_pagination()
            return

        query = self._search_text.strip()
        offset = self._page * PAGE_SIZE
        cid_filter = -1 if self._no_container_mode else None

        if query:
            cards = await db.search(query, limit=PAGE_SIZE, offset=offset)
            total = await db.count_search(query)
            if self._no_container_mode:
                cards = [c for c in cards if not c.get("container_id")]
                total = len(cards)
        else:
            cards = await db.list_cards(limit=PAGE_SIZE, offset=offset, sort="chaos",
                                        container_id=cid_filter)
            total = await db.count_cards(container_id=cid_filter)

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
        self._page_label.setText(
            f"Page {self._page + 1} / {total_pages} ({self._total} cards)"
        )
        self._prev_btn.setEnabled(self._page > 0)
        self._next_btn.setEnabled(self._page + 1 < total_pages)

    # ------------------------------------------------------------------ #
    # UI slots                                                              #
    # ------------------------------------------------------------------ #

    def _on_no_container_toggled(self, checked: bool):
        self._no_container_mode = checked
        self._page = 0
        self._load_page()

    def _on_search_changed(self):
        self._search_text = self._search_edit.text()
        self._page = 0
        self._load_page()

    def _on_id_changed(self):
        self._id_text = self._id_edit.text()
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
        selected_rows = self._table.selectionModel().selectedRows()
        if not selected_rows:
            self._detail.clear()
            return
        if len(selected_rows) > 1:
            self._detail.clear()
            return
        id_item = self._table.item(self._table.currentRow(), 0)
        if id_item is None:
            self._detail.clear()
            return
        card_id = id_item.data(Qt.ItemDataRole.UserRole)
        card = next((c for c in getattr(self, "_cards", []) if c.get("id") == card_id), None)
        if card:
            self._detail.set_card(card)
        else:
            self._detail.clear()

    def _selected_card_ids(self) -> list[int]:
        ids = []
        for idx in self._table.selectionModel().selectedRows():
            item = self._table.item(idx.row(), 0)
            if item:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _on_card_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return

        selected_ids = self._selected_card_ids()
        cards = getattr(self, "_cards", [])

        # Make sure right-clicked row is in selection; if not, treat it as single
        id_item = self._table.item(row, 0)
        if id_item is None:
            return
        clicked_id = id_item.data(Qt.ItemDataRole.UserRole)
        if clicked_id not in selected_ids:
            selected_ids = [clicked_id]

        is_multi = len(selected_ids) > 1
        card = next((c for c in cards if c.get("id") == clicked_id), None)

        menu = QMenu(self)

        # Move to container (works for single or multi)
        move_label = f"↗ Move {len(selected_ids)} cards to container…" if is_multi else "↗ Move to container…"
        move_act = menu.addAction(move_label)
        move_act.triggered.connect(lambda: self._on_move_to_container(selected_ids))

        if not is_multi and card:
            menu.addSeparator()
            resync_act = menu.addAction("↻ Resync from Scryfall")
            resync_act.setEnabled(bool(card.get("scryfall_id")))
            resync_act.triggered.connect(lambda: self._do_resync_card(card))

            history_act = menu.addAction("📈 Price history")
            history_act.setEnabled(bool(card.get("scryfall_id")))
            history_act.triggered.connect(lambda: self._show_price_history(card))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    @asyncSlot()
    async def _on_move_to_container(self, card_ids: list[int]):
        from desktop.db import db

        dlg = _MoveToContainerDialog(self._containers, len(card_ids), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        is_new, container_id, new_name, new_type = dlg.selected_result()
        if is_new:
            try:
                container_id = await db.create_container(new_name, type=new_type)
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Could not create container:\n{exc}")
                return

        await db.move_cards_to_container(card_ids, container_id)
        await self._reload_containers()
        await self._load_page()

    @asyncSlot()
    async def _do_resync_card(self, card: dict):
        from desktop.db import db, scryfall

        sid = card.get("scryfall_id")
        if not sid:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            data = await scryfall.get_by_id(sid)
            if data:
                await db.resync_card(sid, data)
                await self._load_page()
                updated = await db.get_card(card["id"])
                if updated:
                    self._detail.set_card(updated)
            else:
                QMessageBox.warning(self, "Resync", "Card not found on Scryfall.")
        except Exception as exc:
            QMessageBox.warning(self, "Resync error", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def _show_price_history(self, card: dict):
        from desktop.dialogs.price_history import PriceHistoryDialog

        dlg = PriceHistoryDialog(card, parent=self)
        dlg.exec()

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

    @asyncSlot()
    async def refresh(self):
        await self._reload_containers()
        await self._load_page()


# ── Container-picker dialog ───────────────────────────────────────────────────

class _MoveToContainerDialog(QDialog):
    def __init__(self, containers: list[dict], card_count: int, parent=None):
        super().__init__(parent)
        import core.config as cfg

        self._new_containers: list[tuple[str, str]] = []

        self.setWindowTitle("Move to container")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"Move <b>{card_count}</b> card(s) to:"))

        self._combo = QComboBox()
        self._combo.addItem("— Remove from container —", None)
        for c in containers:
            self._combo.addItem(
                f"{c['name']}  [{c.get('type', '')}]",
                c["id"],
            )
        layout.addWidget(self._combo)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        layout.addWidget(QLabel("<b>Create new container:</b>"))

        new_row = QHBoxLayout()
        new_row.setSpacing(6)

        self._new_name_edit = QLineEdit()
        self._new_name_edit.setPlaceholderText("Container name…")
        new_row.addWidget(self._new_name_edit, stretch=2)

        self._new_type_cb = QComboBox()
        self._new_type_cb.addItems(cfg.load().get("container_types", cfg.BUILTIN_TYPES))
        new_row.addWidget(self._new_type_cb, stretch=1)

        add_btn = QPushButton("Add & Select")
        add_btn.setStyleSheet(
            "padding: 4px 10px; background: #0f3460; color: white; border-radius: 3px;"
        )
        add_btn.clicked.connect(self._on_add_new)
        new_row.addWidget(add_btn)
        layout.addLayout(new_row)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep2)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("Move")
        ok_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        self._new_name_edit.returnPressed.connect(self._on_add_new)

    def _on_add_new(self):
        name = self._new_name_edit.text().strip()
        if not name:
            self._new_name_edit.setFocus()
            return
        ctype = self._new_type_cb.currentText()
        idx = len(self._new_containers)
        self._new_containers.append((name, ctype))
        self._combo.addItem(f"✦ {name}  [{ctype}]  (new)", ("NEW", idx))
        self._combo.setCurrentIndex(self._combo.count() - 1)
        self._new_name_edit.clear()

    def selected_result(self) -> tuple[bool, object, str, str]:
        """Return (is_new, container_id, new_name, new_type)."""
        data = self._combo.currentData()
        if isinstance(data, tuple) and data[0] == "NEW":
            name, ctype = self._new_containers[data[1]]
            return True, None, name, ctype
        return False, data, "", ""
