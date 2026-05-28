"""Containers tab widget."""
from __future__ import annotations

import asyncio
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QAbstractItemView, QFrame, QComboBox, QMenu, QApplication,
    QDialog, QDialogButtonBox, QFormLayout, QRadioButton, QButtonGroup,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, QMimeData, QByteArray, QEvent
from PyQt6.QtGui import QColor, QDrag
from qasync import asyncSlot, asyncWrap

from core.i18n import _
from desktop.utils import display_name, lang_flag, format_price
from desktop.widgets.card_detail import CardDetailPanel

_COLUMNS = ["#", "Name", "Set", "CN", "Cond", "Foil", "Lang", "Price (EUR)"]

def _columns():
    return [_("#"), _("Name"), _("Set"), _("CN"), _("Cond"), _("Foil"), _("Lang"), _("Price (EUR)")]


def _cn_sort_key(cn: str) -> float:
    """Numeric sort key for collector numbers ('123', '123a', '★3' → 3.0, '' → ∞)."""
    digits = ""
    for ch in (cn or ""):
        if ch.isdigit():
            digits += ch
        elif digits:
            break  # stop at first non-digit after leading digits
    return float(digits) if digits else float("inf")


class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that compares numerically when both items carry a sort_val."""

    def __init__(self, text: str, sort_val: float | None = None):
        super().__init__(text)
        self._sort_val = sort_val

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _NumericItem):
            sv, ov = self._sort_val, other._sort_val
            if sv is not None and ov is not None:
                return sv < ov
            if sv is None:
                return False   # None sorts last
            if ov is None:
                return True
        return super().__lt__(other)


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
        top_row.addWidget(QLabel(_("<b>Containers</b>")))
        top_row.addStretch()
        self._new_btn = QPushButton(_("+ New container"))
        top_row.addWidget(self._new_btn)
        left_layout.addLayout(top_row)

        # Type filter
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(_("Type:")))
        self._type_filter_combo = QComboBox()
        self._type_filter_combo.addItem(_("— All types —"), None)
        self._type_filter_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        filter_row.addWidget(self._type_filter_combo)
        left_layout.addLayout(filter_row)

        # Container sort
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel(_("Sort:")))
        self._sort_combo = QComboBox()
        self._sort_combo.addItem(_("Name A→Z"), "name_asc")
        self._sort_combo.addItem(_("Cards ↓"), "count_desc")
        self._sort_combo.addItem(_("Value € ↓"), "value_desc")
        self._sort_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        sort_row.addWidget(self._sort_combo)
        left_layout.addLayout(sort_row)

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
        self._rename_btn = QPushButton(_("Rename"))
        self._delete_btn = QPushButton(_("Delete"))
        self._delete_btn.setStyleSheet("color: #e05c5c;")
        self._rename_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        action_row.addWidget(self._rename_btn)
        action_row.addWidget(self._delete_btn)
        action_row.addStretch()
        self._check_deck_btn = QPushButton(_("⚖ Check deck"))
        self._check_deck_btn.setToolTip(_("Check deck legality for the selected format"))
        self._check_deck_btn.setVisible(False)
        action_row.addWidget(self._check_deck_btn)
        self._export_deck_btn = QPushButton(_("↓ Export deck"))
        self._export_deck_btn.setToolTip(_("Export decklist as MTGA/Moxfield-compatible text file"))
        self._export_deck_btn.setVisible(False)
        action_row.addWidget(self._export_deck_btn)
        action_row.addWidget(QLabel(_("Format:")))
        self._format_combo = QComboBox()
        self._format_combo.addItem(_("— no format —"), None)
        self._format_combo.addItem(_("⚔  Commander / EDH"), "commander")
        self._format_combo.addItem(_("Modern"), "modern")
        self._format_combo.addItem(_("Pioneer"), "pioneer")
        self._format_combo.addItem(_("Standard"), "standard")
        self._format_combo.addItem(_("Legacy"), "legacy")
        self._format_combo.addItem(_("Vintage"), "vintage")
        self._format_combo.addItem(_("Pauper"), "pauper")
        self._format_combo.addItem(_("Timeless"), "timeless")
        self._format_combo.addItem(_("Historic"), "historic")
        self._format_combo.setMinimumWidth(160)
        self._format_combo.setEnabled(False)
        action_row.addWidget(self._format_combo)
        action_row.addWidget(QLabel(_("Type:")))
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
        self._table.setHorizontalHeaderLabels(_columns())
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionsClickable(True)
        self._table.setSortingEnabled(True)
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
        self._type_filter_combo.currentIndexChanged.connect(self._refresh_container_list)
        self._sort_combo.currentIndexChanged.connect(self._refresh_container_list)
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
        import core.config as cfg

        self._containers = await db.list_containers()

        # Refresh type filter options (keep current selection if still valid)
        types = cfg.load().get("container_types", [])
        prev_filter = self._type_filter_combo.currentData()
        self._type_filter_combo.blockSignals(True)
        self._type_filter_combo.clear()
        self._type_filter_combo.addItem(_("— All types —"), None)
        for t in types:
            self._type_filter_combo.addItem(t.capitalize(), t)
        idx = self._type_filter_combo.findData(prev_filter)
        self._type_filter_combo.setCurrentIndex(max(0, idx))
        self._type_filter_combo.blockSignals(False)

        self._refresh_container_list()

    def _refresh_container_list(self):
        """Rebuild the container list applying the current type filter and sort."""
        wanted_type = self._type_filter_combo.currentData()   # None = all
        sort_key    = self._sort_combo.currentData()

        containers = self._containers
        if wanted_type is not None:
            containers = [c for c in containers if c.get("type") == wanted_type]

        if sort_key == "name_asc":
            containers = sorted(containers, key=lambda c: c["name"].casefold())
        elif sort_key == "count_desc":
            containers = sorted(containers, key=lambda c: c.get("card_count", 0), reverse=True)
        elif sort_key == "value_desc":
            containers = sorted(containers, key=lambda c: c.get("total_value_eur") or 0.0, reverse=True)

        selected_id = self._selected_container["id"] if self._selected_container else None

        # Deselect if the currently selected container is now filtered out
        if selected_id is not None and not any(c["id"] == selected_id for c in containers):
            self._list.clearSelection()
            self._on_container_selected(None, None)
            selected_id = None

        self._list.blockSignals(True)
        self._list.clear()
        for c in containers:
            count = c.get("card_count", 0)
            value = c.get("total_value_eur") or 0.0
            item = QListWidgetItem(
                f"{c['name']}  [{c.get('type', '')}]  — {count} cards  / €{value:.2f}"
            )
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self._list.addItem(item)
        self._list.blockSignals(False)

        # Reselect the previously selected container if still visible
        if selected_id is not None:
            for i in range(self._list.count()):
                item = self._list.item(i)
                if item and item.data(Qt.ItemDataRole.UserRole) == selected_id:
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

        # Disable sorting during fill to prevent mid-insert re-sorts
        self._table.setSortingEnabled(False)
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
                sort_val: float | None = None,
            ) -> QTableWidgetItem:
                item = (
                    _NumericItem(str(text), sort_val=sort_val)
                    if sort_val is not None
                    else QTableWidgetItem(str(text))
                )
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
            card_id  = card.get("id") or 0
            price    = card.get("price_eur")
            cn_text  = card.get("collector_number") or ""
            self._table.setItem(row_idx, 0, _item(str(card_id), cid=card_id, sort_val=float(card_id)))
            self._table.setItem(row_idx, 1, _item(name_text))
            self._table.setItem(row_idx, 2, _item((card.get("set_code") or "").upper()))
            self._table.setItem(row_idx, 3, _item(cn_text, sort_val=_cn_sort_key(cn_text)))
            self._table.setItem(row_idx, 4, _item(card.get("condition") or ""))
            self._table.setItem(row_idx, 5, _item("★" if card.get("foil") else ""))
            self._table.setItem(row_idx, 6, _item(lang_flag(card)))
            self._table.setItem(row_idx, 7, _item(format_price(price, card.get("price_approx", 0)), sort_val=float(price if price is not None else -1.0)))
        self._table.setSortingEnabled(True)

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
        deck_format = self._selected_container.get("deck_format") if self._selected_container else None
        _show_deck_check(cards, name, deck_format=deck_format, parent=self)

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
            _("Export Deck"),
            f"{safe_name}_{date.today()}.txt",
            _("MTGA/Moxfield (*.txt);;Full with locations (*.txt);;All files (*)"),
        )
        if not path:
            return

        mtga = "Full" not in selected_filter
        text = format_container_decklist(cards, deck_name=deck_name, mtga=mtga)
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, _("Export failed"), str(exc))

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
        menu.addAction(_("↗ Move {noun} to container…").format(noun=noun),
                       lambda: self._on_move_to_container(selected_ids))
        menu.addAction(_("✕ Remove {noun} from container").format(noun=noun),
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
                        act = menu.addAction(_("Remove Commander mark"))
                    else:
                        act = menu.addAction(_("👑 Mark as Commander"))
                    act.triggered.connect(lambda: self._do_toggle_commander(card))
                    menu.addSeparator()

                resync_act = menu.addAction(_("↻ Resync from Scryfall"))
                resync_act.setEnabled(bool(card.get("scryfall_id")))
                resync_act.triggered.connect(lambda: self._do_resync_card(card))

                history_act = menu.addAction(_("📈 Price history"))
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
            QMessageBox.warning(self, _("Commander limit"), err)
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
                QMessageBox.warning(self, _("Resync"), _("Card not found on Scryfall."))
        except Exception as exc:
            QMessageBox.warning(self, _("Resync error"), str(exc))
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
        asyncio.ensure_future(self._do_open_delete_dialog(self._selected_container))

    @asyncSlot()
    async def _do_open_delete_dialog(self, container: dict):
        from desktop.db import db

        container_id = container["id"]
        name         = container["name"]
        card_count   = await db.count_cards_in_container(container_id)

        if card_count == 0:
            reply = QMessageBox.question(
                self, _("Delete container"),
                _("Delete empty container '{name}'?").format(name=name),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                await db.delete_container(container_id)
                self._selected_container = None
                self._detail.clear()
                await self._load_containers()
            return

        # Non-empty container — show choice dialog
        dlg = _DeleteContainerDialog(name, card_count, parent=self)
        if await asyncWrap(dlg.exec) != QDialog.DialogCode.Accepted:
            return

        delete_cards = dlg.delete_cards()

        # Second confirmation
        if delete_cards:
            confirm_msg = _(
                "Really delete container '{name}' and permanently remove "
                "all {count} card(s) from the collection?\n\n"
                "This cannot be undone."
            ).format(name=name, count=card_count)
        else:
            confirm_msg = _(
                "Really delete container '{name}'?\n"
                "The {count} card(s) inside will be kept in the collection "
                "without a container."
            ).format(name=name, count=card_count)

        reply = QMessageBox.warning(
            self, _("Confirm deletion"),
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if delete_cards:
            await db.delete_container_and_cards(container_id)
        else:
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
            self, _("Delete card"),
            _("Remove '{name}' from the collection?").format(name=name),
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
        self.setWindowTitle(_("Move to container"))
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        self._combo = QComboBox()
        self._combo.addItem(_("— Remove from container —"), None)
        for c in containers:
            self._combo.addItem(c["name"], c["id"])
        form = QFormLayout()
        form.addRow(_("Container:"), self._combo)
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


# Formats that use commander/singleton rules (100 cards, 1-2 commanders)
_COMMANDER_FORMATS = {"commander", "oathbreaker", "brawl"}
# Formats that use 60-card rules (max 4 copies of non-basics)
_SIXTY_FORMATS = {
    "standard", "modern", "pioneer", "legacy",
    "vintage", "pauper", "timeless", "historic",
}
# Vintage allows unlimited copies of restricted-list cards; we flag >4 as advisory
_VINTAGE_NOTE = "Vintage: unlimited copies are legal only for cards on the restricted list."


def _show_deck_check(cards: list[dict], deck_name: str,
                     deck_format: str | None = None, parent=None):
    """Dispatch to the format-appropriate deck check dialog."""
    if deck_format in _COMMANDER_FORMATS:
        _show_commander_check(cards, deck_name, parent=parent)
    elif deck_format in _SIXTY_FORMATS:
        _show_sixty_check(cards, deck_name, deck_format=deck_format, parent=parent)
    else:
        # No format set — show a generic card-count summary
        _show_generic_check(cards, deck_name, parent=parent)


def _deck_check_dialog(parent, deck_name: str, issues: list[str],
                       stats_html: str, fmt_label: str = ""):
    """Shared dialog builder for all check variants."""
    from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QScrollArea, QWidget, QDialogButtonBox

    dlg = QDialog(parent)
    title = f"Deck-Check — {deck_name}"
    if fmt_label:
        title += f"  [{fmt_label}]"
    dlg.setWindowTitle(title)
    dlg.setMinimumWidth(500)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)

    if issues:
        summary = QLabel(f"<b style='color:#e05c5c;'>❌ {len(issues)} issue(s) found</b>")
    else:
        summary = QLabel("<b style='color:#7ec8a0;'>✅ Deck is legal</b>")
    summary.setStyleSheet("font-size: 15px; padding: 4px 0;")
    layout.addWidget(summary)

    stats = QLabel(stats_html)
    stats.setWordWrap(True)
    layout.addWidget(stats)

    if issues:
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


def _show_commander_check(cards: list[dict], deck_name: str, parent=None):
    from collections import Counter

    total      = len(cards)
    basics     = [c for c in cards if _is_basic_land(c)]
    nonbasic   = [c for c in cards if not _is_basic_land(c)]
    commanders = [c for c in cards if c.get("is_commander")]

    name_counts = Counter(c.get("name_en") or display_name(c) for c in nonbasic)
    duplicates  = {name: cnt for name, cnt in name_counts.items() if cnt > 1}

    issues: list[str] = []

    if total != 100:
        diff = total - 100
        issues.append(
            f"Card count: {total} / 100  "
            f"({'%+d' % diff} card{'s' if abs(diff) != 1 else ''})"
        )
    if not commanders:
        issues.append("No commander marked  (right-click → 👑 Mark as Commander)")
    elif len(commanders) > 2:
        issues.append(f"{len(commanders)} commanders marked — max 2 allowed (Partner)")
    for name, cnt in sorted(duplicates.items()):
        issues.append(f'Duplicate: "{name}" appears {cnt}×')

    commander_names = "  ·  ".join(display_name(c) for c in commanders) if commanders else "—"
    stats_html = (
        f"Cards: <b>{total}</b>  ·  "
        f"Non-lands: <b>{len(nonbasic)}</b>  ·  "
        f"Basic lands: <b>{len(basics)}</b><br>"
        f"Commander: <b>{commander_names}</b>"
    )
    _deck_check_dialog(parent, deck_name, issues, stats_html, fmt_label="Commander")


def _show_sixty_check(cards: list[dict], deck_name: str,
                      deck_format: str = "modern", parent=None):
    from collections import Counter

    total    = len(cards)
    basics   = [c for c in cards if _is_basic_land(c)]
    nonbasic = [c for c in cards if not _is_basic_land(c)]

    # In 60-card formats, lands that aren't basic can still have max 4 copies
    name_counts = Counter(c.get("name_en") or display_name(c) for c in nonbasic)
    over_four   = {name: cnt for name, cnt in name_counts.items() if cnt > 4}

    issues: list[str] = []

    if total != 60:
        diff = total - 60
        issues.append(
            f"Card count: {total} / 60  "
            f"({'%+d' % diff} card{'s' if abs(diff) != 1 else ''})"
        )
    for name, cnt in sorted(over_four.items()):
        note = " (check restricted list)" if deck_format == "vintage" else ""
        issues.append(f'"{name}" has {cnt} copies (max 4{note})')

    if deck_format == "vintage" and over_four:
        issues.append("ℹ  " + _VINTAGE_NOTE)

    fmt_label = deck_format.capitalize()
    stats_html = (
        f"Cards: <b>{total}</b>  ·  "
        f"Non-basics: <b>{len(nonbasic)}</b>  ·  "
        f"Basic lands: <b>{len(basics)}</b>"
    )
    _deck_check_dialog(parent, deck_name, issues, stats_html, fmt_label=fmt_label)


def _show_generic_check(cards: list[dict], deck_name: str, parent=None):
    """Shown when no format is set — just card count and basic stats."""
    total  = len(cards)
    basics = [c for c in cards if _is_basic_land(c)]
    lands  = [c for c in cards if "Land" in (c.get("type_line") or "")]
    stats_html = (
        f"Cards: <b>{total}</b>  ·  "
        f"Lands: <b>{len(lands)}</b>  ·  "
        f"Basic lands: <b>{len(basics)}</b>"
    )
    issues = ["No format is set for this deck — select a format to enable full legality checks."]
    _deck_check_dialog(parent, deck_name, issues, stats_html, fmt_label="No format")


class _DeleteContainerDialog(QDialog):
    """First-step dialog: choose whether to delete or keep the cards inside."""

    def __init__(self, container_name: str, card_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Delete container"))
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        warn = QLabel(
            _("<b>'{name}'</b> contains <b>{count} card(s)</b>.<br>"
              "What should happen to them?").format(name=container_name, count=card_count)
        )
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(warn)

        self._group = QButtonGroup(self)

        self._keep_rb = QRadioButton(
            _("Keep cards in collection  (unassign from container)")
        )
        self._keep_rb.setChecked(True)
        self._delete_rb = QRadioButton(
            _("Delete cards too  (remove {count} card(s) from the collection permanently)").format(count=card_count)
        )
        self._delete_rb.setStyleSheet("color: #e05c5c;")
        self._group.addButton(self._keep_rb)
        self._group.addButton(self._delete_rb)
        layout.addWidget(self._keep_rb)
        layout.addWidget(self._delete_rb)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def delete_cards(self) -> bool:
        return self._delete_rb.isChecked()
