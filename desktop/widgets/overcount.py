"""Overcount tab — cards with more than N copies in the collection."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSpinBox, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from qasync import asyncSlot

from desktop.utils import lang_flag, format_price


_COLUMNS = ["Name / ID", "Set", "Cond", "Foil", "Lang", "Container", "Price (EUR)"]


class OvercountWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._threshold = 4
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load)

    def refresh(self):
        self._load()

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

        # Tree
        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_COLUMNS))
        self._tree.setHeaderLabels(_COLUMNS)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_COLUMNS)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self._tree)

        self._spin.valueChanged.connect(self._on_threshold_changed)
        self._refresh_btn.clicked.connect(self._load)

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

    # ------------------------------------------------------------------ #
    # Rendering                                                             #
    # ------------------------------------------------------------------ #

    def _populate(self, groups: list[dict]):
        self._tree.clear()

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
            display = f"{printed}  ({name_en})" if printed and printed != name_en else name_en
            total = group["total"]

            parent = QTreeWidgetItem([f"  {display}  ×{total}", "", "", "", "", "", ""])
            parent.setExpanded(True)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setBackground(0, QColor("#1e2a3a"))
            parent.setForeground(0, QColor("#7eb8f7"))

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
                child.setData(0, Qt.ItemDataRole.UserRole, entry.get("id"))
                parent.addChild(child)

            self._tree.addTopLevelItem(parent)
