"""Containers tab widget."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QAbstractItemView, QFrame,
)
from PyQt6.QtCore import Qt, QTimer
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
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        card_splitter.addWidget(self._table)

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
        self._table.itemSelectionChanged.connect(self._on_card_selected)
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
        from desktop.db import db

        cards = await db.list_cards(limit=500, container_id=container_id)
        self._container_cards = cards
        self._table.setRowCount(0)
        for row_idx, card in enumerate(cards):
            self._table.insertRow(row_idx)

            def _item(text: str, cid: int | None = None) -> QTableWidgetItem:
                item = QTableWidgetItem(str(text))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if cid is not None:
                    item.setData(Qt.ItemDataRole.UserRole, cid)
                return item

            self._table.setItem(row_idx, 0, _item(str(card.get("id", "")), card.get("id")))
            self._table.setItem(row_idx, 1, _item(display_name(card)))
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
        self._cont_stats_lbl.setText(
            f"Type: {container.get('type', '—')}  |  {count} cards  |  €{value:.2f}"
        )
        self._rename_btn.setEnabled(True)
        self._delete_btn.setEnabled(count == 0)
        self._detail.clear()
        self._load_container_cards(cid)

    def _on_card_selected(self):
        rows = self._table.selectedItems()
        if not rows:
            self._detail.clear()
            return
        row_idx = self._table.currentRow()
        id_item = self._table.item(row_idx, 0)
        if id_item is None:
            return
        card_id = id_item.data(Qt.ItemDataRole.UserRole)
        cards = getattr(self, "_container_cards", [])
        card = next((c for c in cards if c.get("id") == card_id), None)
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
