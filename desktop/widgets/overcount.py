"""Overcount tab — cards with more than N copies in the collection."""
from __future__ import annotations

import asyncio

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QSpinBox, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QDialog,
    QDialogButtonBox, QFormLayout, QComboBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from qasync import asyncSlot

from desktop.utils import lang_flag, format_price
from desktop.widgets.card_detail import CardDetailPanel


_COLUMNS = ["Name / ID", "Set", "Cond", "Foil", "Lang", "Container", "Price (EUR)"]
_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_CARD_ROLE  = Qt.ItemDataRole.UserRole + 1


class OvercountWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._threshold = 4
        self._containers: list[dict] = []
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load)
        QTimer.singleShot(0, self._load_containers)

    def refresh(self):
        self._load()
        self._load_containers()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<h2>Overcounted Cards</h2>"))
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Threshold ≥"))
        self._spin = QSpinBox()
        self._spin.setRange(2, 99)
        self._spin.setValue(self._threshold)
        self._spin.setFixedWidth(60)
        toolbar.addWidget(self._spin)
        self._refresh_btn = QPushButton("Refresh")
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(self._status)

        # Main splitter: tree | detail panel
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_COLUMNS)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self._tree)

        self._detail = CardDetailPanel(show_buttons=False)
        self._detail.setMinimumWidth(260)
        self._detail.setMaximumWidth(360)
        splitter.addWidget(self._detail)
        splitter.setSizes([660, 300])

        root.addWidget(splitter)

        self._spin.valueChanged.connect(self._on_threshold_changed)
        self._refresh_btn.clicked.connect(self._load)
        self._tree.currentItemChanged.connect(self._on_item_selected)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    def _on_threshold_changed(self, value: int):
        self._threshold = value
        self._load()

    @asyncSlot()
    async def _load(self):
        import core.config as cfg
        from desktop.db import db

        excluded = cfg.load().get("overcount_excluded_types", [])
        cards = await db.get_overcount_cards(threshold=self._threshold, excluded_types=excluded)
        self._populate(cards)

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db
        try:
            self._containers = await db.list_containers()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Rendering                                                             #
    # ------------------------------------------------------------------ #

    def _populate(self, groups: list[dict]):
        self._tree.clear()
        self._detail.clear()

        if not groups:
            self._status.setText(f"No cards with {self._threshold}+ copies.")
            return

        total_entries = sum(g["total"] for g in groups)
        self._status.setText(
            f"{len(groups)} unique card(s)  ·  {total_entries} total copies"
        )

        for group in groups:
            name_en = group.get("name_en") or ""
            printed = group.get("printed_name") or group.get("name_de") or ""
            disp = f"{printed}  ({name_en})" if printed and printed != name_en else name_en
            total = group["total"]

            parent_item = QTreeWidgetItem([f"  {disp}  ×{total}", "", "", "", "", "", ""])
            parent_item.setExpanded(True)
            font = parent_item.font(0)
            font.setBold(True)
            parent_item.setFont(0, font)
            parent_item.setBackground(0, QColor("#1e2a3a"))
            parent_item.setForeground(0, QColor("#7eb8f7"))

            for entry in group["entries"]:
                set_info = f"{(entry.get('set_code') or '').upper()} #{entry.get('collector_number') or ''}"
                child = QTreeWidgetItem([
                    f"    ID {entry.get('id', '?')}",
                    set_info,
                    entry.get("condition") or "",
                    "★" if entry.get("foil") else "",
                    lang_flag(entry),
                    entry.get("container_name") or "—",
                    format_price(entry.get("price_eur")),
                ])
                child.setData(0, _ENTRY_ROLE, entry.get("id"))
                child.setData(0, _CARD_ROLE, entry)
                parent_item.addChild(child)

            self._tree.addTopLevelItem(parent_item)

    # ------------------------------------------------------------------ #
    # Interaction                                                           #
    # ------------------------------------------------------------------ #

    def _on_item_selected(self, current: QTreeWidgetItem, _prev):
        if current is None or current.parent() is None:
            self._detail.clear()
            return
        entry = current.data(0, _CARD_ROLE)
        if entry:
            self._detail.set_card(entry)

    def _selected_child_ids(self) -> list[int]:
        seen: set[int] = set()
        ids: list[int] = []
        for item in self._tree.selectedItems():
            if item.parent() is None:
                continue
            cid = item.data(0, _ENTRY_ROLE)
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        return ids

    def _on_context_menu(self, pos):
        card_ids = self._selected_child_ids()
        if not card_ids:
            return
        n = len(card_ids)
        noun = f"{n} card{'s' if n > 1 else ''}"
        menu = QMenu(self)
        menu.addAction(f"↗ Move {noun} to container…",
                       lambda: self._on_move_to_container(card_ids))
        menu.addAction(f"✕ Remove {noun} from container",
                       lambda: asyncio.ensure_future(self._do_move_cards(card_ids, None)))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _on_move_to_container(self, card_ids: list[int]):
        dlg = _MoveToContainerDialog(self._containers, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            asyncio.ensure_future(self._do_move_cards(card_ids, dlg.selected_id()))

    async def _do_move_cards(self, card_ids: list[int], container_id):
        from desktop.db import db
        try:
            await db.move_cards_to_container(card_ids, container_id)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))


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
