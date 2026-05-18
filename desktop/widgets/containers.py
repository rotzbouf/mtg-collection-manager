"""Containers tab widget."""
from __future__ import annotations

import asyncio
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QAbstractItemView, QFrame, QComboBox, QMenu, QApplication,
    QDialog, QDialogButtonBox, QFormLayout,
)
from PyQt6.QtCore import Qt, QTimer, QMimeData, QByteArray, QEvent
from PyQt6.QtGui import QColor, QDrag
from qasync import asyncSlot

from desktop.utils import display_name, lang_flag, format_price
from desktop.widgets.card_detail import CardDetailPanel

_COLUMNS = ["#", "Name", "Set", "CN", "Cond", "Foil", "Lang", "Price (EUR)"]


class ContainersWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._containers: list[dict] = []
        self._selected_container: Optional[dict] = None
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_containers)

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left: container list ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("<b>Containers</b>"))
        top_row.addStretch()
        self._new_btn = QPushButton("+ New container")
        top_row.addWidget(self._new_btn)
        left_layout.addLayout(top_row)

        self._list = QListWidget()
        left_layout.addWidget(self._list)
        left.setMaximumWidth(300)

        splitter.addWidget(left)

        # ---- Right: container detail ----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        # Container info header
        self._cont_name_lbl = QLabel("")
        self._cont_name_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        self._cont_stats_lbl = QLabel("")
        right_layout.addWidget(self._cont_name_lbl)
        right_layout.addWidget(self._cont_stats_lbl)

        # Container action buttons
        action_row = QHBoxLayout()
        self._rename_btn = QPushButton("Rename")
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setStyleSheet("color: #e05c5c;")
        self._rename_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        action_row.addWidget(self._rename_btn)
        action_row.addWidget(self._delete_btn)
        action_row.addStretch()
        self._check_deck_btn = QPushButton("⚖ Check deck")
        self._check_deck_btn.setToolTip("Check deck legality (commander: singleton rule, 100 cards)")
        self._check_deck_btn.setVisible(False)
        action_row.addWidget(self._check_deck_btn)
        self._export_deck_btn = QPushButton("↓ Export deck")
        self._export_deck_btn.setToolTip("Export decklist as MTGA/Moxfield-compatible text file")
        self._export_deck_btn.setVisible(False)
        action_row.addWidget(self._export_deck_btn)
        action_row.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        self._format_combo.addItem("— no format —", None)
        self._format_combo.addItem("⚔ Commander", "commander")
        self._format_combo.addItem("60-card Standard", "standard")
        self._format_combo.addItem("60-card Timeless", "timeless")
        self._format_combo.setMinimumWidth(140)
        self._format_combo.setEnabled(False)
        action_row.addWidget(self._format_combo)
        action_row.addWidget(QLabel("Type:"))
        self._type_combo = QComboBox()
        self._type_combo.setMinimumWidth(100)
        self._type_combo.setEnabled(False)
        action_row.addWidget(self._type_combo)
        right_layout.addLayout(action_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        right_layout.addWidget(sep)

        # Card table + detail
        card_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Card table
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.viewport().installEventFilter(self)
        card_splitter.addWidget(self._table)

        # Accept drops on the container list
        self._list.setAcceptDrops(True)
        self._list.viewport().setAcceptDrops(True)
        self._list.viewport().installEventFilter(self)

        self._detail = CardDetailPanel(show_buttons=True)
        self._detail.setMinimumWidth(260)
        self._detail.setMaximumWidth(320)
        card_splitter.addWidget(self._detail)
        card_splitter.setSizes([500, 280])

        right_layout.addWidget(card_splitter)
        splitter.addWidget(right)
        splitter.setSizes([280, 720])

        root.addWidget(splitter)

        # Signals
        self._list.currentItemChanged.connect(self._on_container_selected)
        self._new_btn.clicked.connect(self._on_new_container)
        self._rename_btn.clicked.connect(self._on_rename_container)
        self._delete_btn.clicked.connect(self._on_delete_container)
        self._format_combo.currentIndexChanged.connect(self._on_deck_format_changed)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        self._check_deck_btn.clicked.connect(self._on_check_deck)
        self._export_deck_btn.clicked.connect(self._on_export_deck)
        self._table.itemSelectionChanged.connect(self._on_card_selected)
        self._table.customContextMenuRequested.connect(self._on_card_context_menu)
        self._detail.edit_requested.connect(self._on_edit_card)
        self._detail.delete_requested.connect(self._on_delete_card)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db

        self._containers = await db.list_containers()
        prev_id = self._selected_container["id"] if self._selected_container else None

        self._list.blockSignals(True)
        self._list.clear()
        for c in self._containers:
            count = c.get("card_count", 0)
            value = c.get("total_value_eur") or 0.0
            item = QListWidgetItem(
                f"{c['name']}  [{c.get('type', '')}]  — {count} cards  / €{value:.2f}"
            )
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self._list.addItem(item)
        self._list.blockSignals(False)

        # Reselect the previously selected container
        if prev_id is not None:
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == prev_id:
                    self._list.setCurrentItem(item)
                    break

    @asyncSlot()
    async def _load_container_cards(self, container_id: int):
        from collections import Counter
        from desktop.db import db

        cards = await db.list_cards(limit=500, container_id=container_id)
        # Commanders always on top
        cards = sorted(cards, key=lambda c: 0 if c.get("is_commander") else 1)
        self._container_cards = cards

        # Detect duplicates for commander-format decks
        deck_format = self._selected_container.get("deck_format") if self._selected_container else None
        is_commander_deck = deck_format == "commander"
        if is_commander_deck:
            name_counts: Counter = Counter(
                (c.get("name_en") or "").lower()
                for c in cards
                if not _is_basic_land(c)
            )
            duplicate_names = {name for name, cnt in name_counts.items() if cnt > 1}
        else:
            duplicate_names: set = set()

        self._table.setRowCount(0)
        for row_idx, card in enumerate(cards):
            self._table.insertRow(row_idx)
            is_cmd = bool(card.get("is_commander"))
            is_dup = (
                is_commander_deck
                and not _is_basic_land(card)
                and (card.get("name_en") or "").lower() in duplicate_names
            )

            def _item(
                text: str,
                cid: int | None = None,
                commander: bool = is_cmd,
                duplicate: bool = is_dup,
            ) -> QTableWidgetItem:
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if cid is not None:
                    item.setData(Qt.ItemDataRole.UserRole, cid)
                if commander:
                    item.setBackground(QColor("#1e1600"))
                    item.setForeground(QColor("#f0c040"))
                elif duplicate:
                    item.setBackground(QColor("#3a1010"))
                    item.setForeground(QColor("#e07070"))
                return item

            name_text = f"👑 {display_name(card)}" if is_cmd else display_name(card)
            if is_dup:
                name_text = f"⚠ {name_text}"
            self._table.setItem(row_idx, 0, _item(str(card.get("id", "")), card.get("id")))
            self._table.setItem(row_idx, 1, _item(name_text))
            self._table.setItem(row_idx, 2, _item((card.get("set_code") or "").upper()))
            self._table.setItem(row_idx, 3, _item(card.get("collector_number") or ""))
            self._table.setItem(row_idx, 4, _item(card.get("condition") or ""))
            self._table.setItem(row_idx, 5, _item("★" if card.get("foil") else ""))
            self._table.setItem(row_idx, 6, _item(lang_flag(card)))
            self._table.setItem(row_idx, 7, _item(format_price(card.get("price_eur"))))

    # ------------------------------------------------------------------ #
    # Slots                                                                 #
    # ------------------------------------------------------------------ #

    def _on_container_selected(self, current, _previous):
        if current is None:
            self._selected_container = None
            self._cont_name_lbl.setText("")
            self._cont_stats_lbl.setText("")
            self._rename_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            self._type_combo.setEnabled(False)
            self._format_combo.setEnabled(False)
            self._check_deck_btn.setVisible(False)
            self._export_deck_btn.setVisible(False)
            self._table.setRowCount(0)
            self._detail.clear()
            return

        cid = current.data(Qt.ItemDataRole.UserRole)
        container = next((c for c in self._containers if c["id"] == cid), None)
        if container is None:
            return

        self._selected_container = container
        self._cont_name_lbl.setText(container["name"])
        count = container.get("card_count", 0)
        value = container.get("total_value_eur") or 0.0
        self._cont_stats_lbl.setText(f"{count} cards  |  €{value:.2f}")

        # Populate type combo
        import core.config as cfg
        types = cfg.load().get("container_types", [])
        self._type_combo.blockSignals(True)
        self._type_combo.clear()
        self._type_combo.addItems(types)
        current_type = container.get("type", "")
        if current_type in types:
            self._type_combo.setCurrentText(current_type)
        self._type_combo.blockSignals(False)
        self._type_combo.setEnabled(True)

        # Populate deck format combo
        self._format_combo.blockSignals(True)
        deck_format = container.get("deck_format")
        idx = self._format_combo.findData(deck_format)
        self._format_combo.setCurrentIndex(max(0, idx))
        self._format_combo.blockSignals(False)
        self._format_combo.setEnabled(True)

        is_deck = deck_format is not None
        self._check_deck_btn.setVisible(is_deck)
        self._export_deck_btn.setVisible(is_deck)

        self._rename_btn.setEnabled(True)
        self._delete_btn.setEnabled(count == 0)
        self._detail.clear()
        self._load_container_cards(cid)

    @asyncSlot(str)
    async def _on_type_changed(self, new_type: str):
        if self._selected_container is None or not new_type:
            return
        from desktop.db import db

        await db.update_container_type(self._selected_container["id"], new_type)
        self._selected_container["type"] = new_type
        await self._load_containers()

    @asyncSlot(int)
    async def _on_deck_format_changed(self, _index: int):
        if self._selected_container is None:
            return
        from desktop.db import db

        deck_format = self._format_combo.currentData()
        await db.set_container_deck_format(self._selected_container["id"], deck_format)
        self._selected_container["deck_format"] = deck_format
        is_deck = deck_format is not None
        self._check_deck_btn.setVisible(is_deck)
        self._export_deck_btn.setVisible(is_deck)
        await self._load_containers()
        await self._load_container_cards(self._selected_container["id"])

    # ------------------------------------------------------------------ #
    # Drag & drop — card table → container list                            #
    # ------------------------------------------------------------------ #

    _MIME_TYPE = "application/x-mtg-card-ids"

    def eventFilter(self, obj, event):
        if obj is self._table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                self._drag_start = event.pos()
            elif event.type() == QEvent.Type.MouseMove:
                if (
                    event.buttons() & Qt.MouseButton.LeftButton
                    and hasattr(self, "_drag_start")
                    and (event.pos() - self._drag_start).manhattanLength()
                    > QApplication.startDragDistance()
                ):
                    self._start_drag()
                    return True

        elif obj is self._list.viewport():
            if event.type() == QEvent.Type.DragEnter:
                if event.mimeData().hasFormat(self._MIME_TYPE):
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.DragMove:
                if event.mimeData().hasFormat(self._MIME_TYPE):
                    item = self._list.itemAt(event.position().toPoint())
                    self._list.setCurrentItem(item)
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop:
                return self._handle_drop(event)

        return super().eventFilter(obj, event)

    def _start_drag(self):
        ids = []
        for idx in self._table.selectionModel().selectedRows():
            item = self._table.item(idx.row(), 0)
            if item:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        if not ids:
            return

        mime = QMimeData()
        mime.setData(self._MIME_TYPE, QByteArray(",".join(str(i) for i in ids).encode()))

        drag = QDrag(self._table)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def _handle_drop(self, event) -> bool:
        item = self._list.itemAt(event.position().toPoint())
        if item is None:
            return False
        container_id = item.data(Qt.ItemDataRole.UserRole)
        raw = event.mimeData().data(self._MIME_TYPE)
        try:
            card_ids = [int(x) for x in bytes(raw).decode().split(",") if x]
        except ValueError:
            return False
        if not card_ids:
            return False
        event.acceptProposedAction()
        self._do_move_cards_dnd(card_ids, container_id)
        return True

    @asyncSlot()
    async def _do_move_cards_dnd(self, card_ids: list[int], container_id: int):
        from desktop.db import db
        await db.move_cards_to_container(card_ids, container_id)
        await self._load_containers()
        if self._selected_container:
            await self._load_container_cards(self._selected_container["id"])

    def _on_check_deck(self):
        cards = getattr(self, "_container_cards", [])
        name = self._selected_container.get("name", "Deck") if self._selected_container else "Deck"
        _show_commander_check(cards, name, parent=self)

    def _on_export_deck(self):
        from pathlib import Path
        from datetime import date
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        from core.deckbuilder import format_container_decklist

        cards = getattr(self, "_container_cards", [])
        deck_name = self._selected_container.get("name", "deck") if self._selected_container else "deck"
        safe_name = deck_name.replace(" ", "_")

        # Offer both export formats via two filter choices
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Deck",
            f"{safe_name}_{date.today()}.txt",
            "MTGA/Moxfield (*.txt);;Full with locations (*.txt);;All files (*)",
        )
        if not path:
            return

        mtga = "Full" not in selected_filter
        text = format_container_decklist(cards, deck_name=deck_name, mtga=mtga)
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    def _selected_card_ids(self) -> list[int]:
        seen: set[int] = set()
        ids: list[int] = []
        for idx in self._table.selectionModel().selectedRows():
            item = self._table.item(idx.row(), 0)
            if item:
                cid = item.data(Qt.ItemDataRole.UserRole)
                if cid not in seen:
                    seen.add(cid)
                    ids.append(cid)
        return ids

    def _on_card_context_menu(self, pos):
        selected_ids = self._selected_card_ids()
        if not selected_ids:
            return

        cards = getattr(self, "_container_cards", [])
        n = len(selected_ids)
        noun = f"{n} card{'s' if n > 1 else ''}"

        menu = QMenu(self)

        # Multi-card actions (always shown)
        menu.addAction(f"↗ Move {noun} to container…",
                       lambda: self._on_move_to_container(selected_ids))
        menu.addAction(f"✕ Remove {noun} from container",
                       lambda: asyncio.ensure_future(
                           self._do_move_cards(selected_ids, None)))

        # Single-card only actions
        if n == 1:
            card = next((c for c in cards if c.get("id") == selected_ids[0]), None)
            if card:
                deck_format = self._selected_container.get("deck_format") if self._selected_container else None
                menu.addSeparator()
                if deck_format == "commander":
                    if card.get("is_commander"):
                        act = menu.addAction("Remove Commander mark")
                    else:
                        act = menu.addAction("👑 Mark as Commander")
                    act.triggered.connect(lambda: self._do_toggle_commander(card))
                    menu.addSeparator()

                resync_act = menu.addAction("↻ Resync from Scryfall")
                resync_act.setEnabled(bool(card.get("scryfall_id")))
                resync_act.triggered.connect(lambda: self._do_resync_card(card))

                history_act = menu.addAction("📈 Price history")
                history_act.setEnabled(bool(card.get("scryfall_id")))
                history_act.triggered.connect(lambda: self._show_price_history(card))

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_move_to_container(self, card_ids: list[int]):
        dlg = _MoveToContainerDialog(self._containers, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            asyncio.ensure_future(self._do_move_cards(card_ids, dlg.selected_id()))

    async def _do_move_cards(self, card_ids: list[int], container_id):
        from desktop.db import db
        try:
            await db.move_cards_to_container(card_ids, container_id)
            await self._load_containers()
            if self._selected_container:
                await self._load_container_cards(self._selected_container["id"])
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    @asyncSlot()
    async def _do_toggle_commander(self, card: dict):
        from desktop.db import db

        container_id = self._selected_container["id"] if self._selected_container else None
        if container_id is None:
            return

        new_value = not bool(card.get("is_commander"))
        ok, err = await db.set_commander(card["id"], new_value, container_id)
        if not ok:
            QMessageBox.warning(self, "Commander limit", err)
            return

        await self._load_container_cards(container_id)
        # Restore card selection in detail panel
        updated = next((c for c in self._container_cards if c.get("id") == card["id"]), None)
        if updated:
            self._detail.set_card(updated)

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
                if self._selected_container:
                    await self._load_container_cards(self._selected_container["id"])
                updated = next(
                    (c for c in self._container_cards if c.get("id") == card["id"]), None
                )
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

    def _on_card_selected(self):
        selected = self._selected_card_ids()
        if len(selected) != 1:
            self._detail.clear()
            return
        cards = getattr(self, "_container_cards", [])
        card = next((c for c in cards if c.get("id") == selected[0]), None)
        if card:
            self._detail.set_card(card)

    def _on_new_container(self):
        from desktop.dialogs.container_dialog import ContainerDialog

        dlg = ContainerDialog(mode="create", parent=self)
        dlg.confirmed.connect(self._do_create_container)
        dlg.exec()

    @asyncSlot(str, str)
    async def _do_create_container(self, name: str, ctype: str):
        from desktop.db import db

        await db.create_container(name, type=ctype)
        await self._load_containers()

    def _on_rename_container(self):
        if self._selected_container is None:
            return
        from desktop.dialogs.container_dialog import ContainerDialog

        dlg = ContainerDialog(
            mode="rename",
            current_name=self._selected_container["name"],
            parent=self,
        )
        dlg.confirmed.connect(self._do_rename_container)
        dlg.exec()

    @asyncSlot(str, str)
    async def _do_rename_container(self, name: str, _ctype: str):
        from desktop.db import db

        if self._selected_container:
            await db.rename_container(self._selected_container["id"], name)
            await self._load_containers()

    def _on_delete_container(self):
        if self._selected_container is None:
            return
        name = self._selected_container["name"]
        reply = QMessageBox.question(
            self, "Delete container",
            f"Delete container '{name}'?\nCards will be moved out of it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_delete_container(self._selected_container["id"])

    @asyncSlot()
    async def _do_delete_container(self, container_id: int):
        from desktop.db import db

        await db.delete_container(container_id)
        self._selected_container = None
        self._detail.clear()
        await self._load_containers()

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
        if self._selected_container:
            await self._load_container_cards(self._selected_container["id"])
        updated = await db.get_card(card_id)
        if updated:
            self._detail.set_card(updated)

    def _on_delete_card(self, card: dict):
        name = display_name(card)
        reply = QMessageBox.question(
            self, "Delete card",
            f"Remove '{name}' from the collection?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_delete_card(card["id"])

    @asyncSlot()
    async def _do_delete_card(self, card_id: int):
        from desktop.db import db

        await db.remove_card(card_id)
        self._detail.clear()
        if self._selected_container:
            await self._load_container_cards(self._selected_container["id"])
        await self._load_containers()

    @asyncSlot()
    async def refresh(self):
        await self._load_containers()


class _MoveToContainerDialog(QDialog):
    def __init__(self, containers: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move to container")
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        self._combo = QComboBox()
        self._combo.addItem("— Remove from container —", None)
        for c in containers:
            self._combo.addItem(c["name"], c["id"])
        form = QFormLayout()
        form.addRow("Container:", self._combo)
        layout.addLayout(form)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_id(self):
        return self._combo.currentData()


# ── Commander deck legality check ─────────────────────────────────────────────

# Basic lands that are exempt from the singleton rule.
_BASIC_LAND_NAMES = {
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest", "Snow-Covered Wastes",
}


def _is_basic_land(card: dict) -> bool:
    if card.get("name_en", "") in _BASIC_LAND_NAMES:
        return True
    # type_line may be English ("Basic Land — Plains") or localized;
    # the English check covers most cases, the name check covers the rest.
    return (card.get("type_line") or "").startswith("Basic Land")


def _show_commander_check(cards: list[dict], deck_name: str, parent=None):
    from collections import Counter
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QWidget, QDialogButtonBox

    total = len(cards)
    basics   = [c for c in cards if _is_basic_land(c)]
    nonbasic = [c for c in cards if not _is_basic_land(c)]
    commanders = [c for c in cards if c.get("is_commander")]

    # Singleton check: count non-basic cards by English name
    name_counts = Counter(c.get("name_en") or display_name(c) for c in nonbasic)
    duplicates  = {name: cnt for name, cnt in name_counts.items() if cnt > 1}

    issues: list[str] = []

    if total != 100:
        diff = total - 100
        issues.append(
            f"Kartenzahl: {total} / 100  "
            f"({'%+d' % diff} Karte{'n' if abs(diff) != 1 else ''})"
        )

    if not commanders:
        issues.append("Kein Commander markiert  (Rechtsklick → 👑 Mark as Commander)")
    elif len(commanders) > 2:
        issues.append(f"{len(commanders)} Commander markiert — maximal 2 erlaubt (Partner)")

    for name, cnt in sorted(duplicates.items()):
        issues.append(f'Duplikat: „{name}" kommt {cnt}× vor')

    # ── Build dialog ──────────────────────────────────────────────────────
    dlg = QDialog(parent)
    dlg.setWindowTitle(f"Deck-Check — {deck_name}")
    dlg.setMinimumWidth(480)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)

    # Summary line
    if issues:
        summary = QLabel(f"<b style='color:#e05c5c;'>❌ {len(issues)} Problem(e) gefunden</b>")
    else:
        summary = QLabel("<b style='color:#7ec8a0;'>✅ Deck ist legal</b>")
    summary.setStyleSheet("font-size: 15px; padding: 4px 0;")
    layout.addWidget(summary)

    # Stats
    commander_names = "  ·  ".join(
        display_name(c) for c in commanders
    ) if commanders else "—"
    stats = QLabel(
        f"Karten gesamt: <b>{total}</b>  ·  "
        f"Nicht-Länder: <b>{len(nonbasic)}</b>  ·  "
        f"Standardländer: <b>{len(basics)}</b><br>"
        f"Commander: <b>{commander_names}</b>"
    )
    stats.setWordWrap(True)
    layout.addWidget(stats)

    if issues:
        from PyQt6.QtWidgets import QFrame
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        layout.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(4)
        for issue in issues:
            lbl = QLabel(f"• {issue}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #e07070; font-size: 12px;")
            inner_layout.addWidget(lbl)
        inner_layout.addStretch()
        scroll.setWidget(inner)
        scroll.setMaximumHeight(min(40 + len(issues) * 28, 300))
        layout.addWidget(scroll)

    btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    btns.accepted.connect(dlg.accept)
    layout.addWidget(btns)

    dlg.exec()
