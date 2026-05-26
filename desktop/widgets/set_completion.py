"""Set completion tracker desktop widget."""
from __future__ import annotations

import asyncio
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QProgressBar,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from qasync import asyncSlot

from desktop.utils import format_price, display_name, RARITY_COLORS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rarity_order(r: str) -> int:
    return {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3}.get(r.lower(), 4)


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem whose sort order is driven by its UserRole value.

    Qt calls ``__lt__`` when sorting; the default implementation compares
    display text as a string, making numeric columns sort lexicographically
    ("10" < "2").  Storing a numeric sort key in ``UserRole`` and overriding
    ``__lt__`` here fixes that without changing displayed text.
    """

    def __lt__(self, other: "QTableWidgetItem") -> bool:  # type: ignore[override]
        my_val = self.data(Qt.ItemDataRole.UserRole)
        other_val = other.data(Qt.ItemDataRole.UserRole)
        if my_val is not None and other_val is not None:
            try:
                return my_val < other_val  # type: ignore[operator]
            except TypeError:
                pass
        return super().__lt__(other)


def _num_item(value, decimals: int = 2) -> _SortableItem:
    """Right-aligned numeric table item that sorts by real value."""
    if value is None:
        item = _SortableItem("—")
        item.setData(Qt.ItemDataRole.UserRole, -1.0)
    else:
        text = str(int(value)) if decimals == 0 else f"€{value:.{decimals}f}"
        item = _SortableItem(text)
        item.setData(Qt.ItemDataRole.UserRole, float(value))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


# ── Main widget ───────────────────────────────────────────────────────────────

class SetCompletionWidget(QWidget):
    """Two-panel widget: set list on the left, set detail on the right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_ready = False
        self._sets: list[dict] = []
        self._current_cards: list[dict] = []
        self._current_set_code: str = ""
        self._build_ui()

    # ── DB lifecycle ──────────────────────────────────────────────────────────

    def db_ready(self):
        self._db_ready = True
        self._load_sets()

    def refresh(self):
        if self._db_ready:
            self._load_sets()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: set list ────────────────────────────────────────────────────
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 4, 0)
        left_lay.setSpacing(4)

        # Toolbar
        tb = QHBoxLayout()
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter sets…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.textChanged.connect(self._apply_filter)
        tb.addWidget(self._filter_edit)
        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(28)
        refresh_btn.setToolTip("Reload sets")
        refresh_btn.clicked.connect(self._load_sets)
        tb.addWidget(refresh_btn)
        left_lay.addLayout(tb)

        self._set_table = QTableWidget(0, 5)
        self._set_table.setHorizontalHeaderLabels(
            ["Code", "Set name", "Copies", "Distinct", "Value €"]
        )
        hh = self._set_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._set_table.setColumnWidth(0, 50)
        self._set_table.setColumnWidth(2, 55)
        self._set_table.setColumnWidth(3, 60)
        self._set_table.setColumnWidth(4, 75)
        self._set_table.setAlternatingRowColors(True)
        self._set_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._set_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._set_table.setSortingEnabled(True)
        self._set_table.verticalHeader().setVisible(False)
        self._set_table.itemSelectionChanged.connect(self._on_set_selected)
        left_lay.addWidget(self._set_table)

        self._sets_status = QLabel("")
        self._sets_status.setStyleSheet("color: #888; font-size: 10px;")
        left_lay.addWidget(self._sets_status)

        splitter.addWidget(left)

        # ── Right: set detail ─────────────────────────────────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)
        right_lay.setSpacing(6)

        # Set header
        self._set_name_lbl = QLabel("Select a set")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        self._set_name_lbl.setFont(f)
        right_lay.addWidget(self._set_name_lbl)

        self._set_meta_lbl = QLabel("")
        self._set_meta_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._set_meta_lbl.setWordWrap(True)
        right_lay.addWidget(self._set_meta_lbl)

        # Completion bar row
        bar_row = QHBoxLayout()
        self._owned_lbl = QLabel("")
        bar_row.addWidget(self._owned_lbl)
        bar_row.addStretch()
        self._pct_lbl = QLabel("")
        self._pct_lbl.setStyleSheet("font-weight: bold;")
        bar_row.addWidget(self._pct_lbl)
        right_lay.addLayout(bar_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)
        self._progress.setVisible(False)
        right_lay.addWidget(self._progress)

        # Summary row
        self._value_lbl = QLabel("")
        self._value_lbl.setStyleSheet("color: #888; font-size: 10px;")
        right_lay.addWidget(self._value_lbl)

        # Card table
        self._card_table = QTableWidget(0, 7)
        self._card_table.setHorizontalHeaderLabels(
            ["#", "Card name", "Rarity", "Lang", "Cond", "Foil", "Price €"]
        )
        ch = self._card_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ch.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self._card_table.setColumnWidth(0, 42)
        self._card_table.setColumnWidth(2, 72)
        self._card_table.setColumnWidth(3, 38)
        self._card_table.setColumnWidth(4, 40)
        self._card_table.setColumnWidth(5, 32)
        self._card_table.setColumnWidth(6, 72)
        self._card_table.setAlternatingRowColors(True)
        self._card_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._card_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._card_table.setSortingEnabled(True)
        self._card_table.verticalHeader().setVisible(False)
        right_lay.addWidget(self._card_table)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        root.addWidget(splitter)

    # ── Data loading ──────────────────────────────────────────────────────────

    @asyncSlot()
    async def _load_sets(self):
        from desktop.db import db

        self._sets_status.setText("Loading…")
        try:
            self._sets = await db.get_sets_summary()
        except Exception as exc:
            self._sets_status.setText(f"Error: {exc}")
            return

        self._render_set_table(self._sets)
        total_sets = len(self._sets)
        total_copies = sum(s.get("card_count", 0) for s in self._sets)
        self._sets_status.setText(
            f"{total_sets} sets · {total_copies} total copies"
        )

    def _render_set_table(self, sets: list[dict]):
        self._set_table.blockSignals(True)
        self._set_table.setSortingEnabled(False)
        self._set_table.setRowCount(0)
        self._set_table.setRowCount(len(sets))

        for row_idx, s in enumerate(sets):
            code_item = QTableWidgetItem(s.get("set_code") or "")
            code_item.setData(Qt.ItemDataRole.UserRole, s.get("set_code", ""))
            name_item = QTableWidgetItem(s.get("set_name") or s.get("set_code") or "")
            copies_item = _num_item(s.get("card_count", 0), decimals=0)
            distinct_item = _num_item(s.get("distinct_names", 0), decimals=0)
            value_item = _num_item(s.get("total_value_eur"), decimals=2)

            self._set_table.setItem(row_idx, 0, code_item)
            self._set_table.setItem(row_idx, 1, name_item)
            self._set_table.setItem(row_idx, 2, copies_item)
            self._set_table.setItem(row_idx, 3, distinct_item)
            self._set_table.setItem(row_idx, 4, value_item)

        self._set_table.setSortingEnabled(True)
        self._set_table.blockSignals(False)

    def _apply_filter(self, text: str):
        needle = text.lower().strip()
        filtered = [
            s for s in self._sets
            if (not needle)
            or needle in (s.get("set_code") or "").lower()
            or needle in (s.get("set_name") or "").lower()
        ]
        self._render_set_table(filtered)

    # ── Set selection ─────────────────────────────────────────────────────────

    @asyncSlot()
    async def _on_set_selected(self):
        row = self._set_table.currentRow()
        if row < 0:
            return
        code_item = self._set_table.item(row, 0)
        if code_item is None:
            return
        set_code = code_item.data(Qt.ItemDataRole.UserRole)
        if not set_code or set_code == self._current_set_code:
            return
        self._current_set_code = set_code
        await self._load_set_detail(set_code)

    @asyncSlot()
    async def _load_set_detail(self, set_code: str):
        from desktop.db import db, scryfall

        self._set_name_lbl.setText(f"Loading {set_code.upper()}…")
        self._set_meta_lbl.setText("")
        self._owned_lbl.setText("")
        self._pct_lbl.setText("")
        self._progress.setVisible(False)
        self._value_lbl.setText("")
        self._card_table.setRowCount(0)

        try:
            cards = await db.get_collection_by_set(set_code)
        except Exception as exc:
            self._set_name_lbl.setText(f"Error loading {set_code}: {exc}")
            return

        self._current_cards = cards

        if not cards:
            self._set_name_lbl.setText(f"{set_code.upper()} — no cards in collection")
            return

        # Aggregate
        set_name = cards[0].get("set_name") or set_code.upper()
        distinct = len({c.get("name_en") or c.get("printed_name") or "" for c in cards} - {""})
        total_copies = len(cards)
        total_value = sum(c.get("price_eur") or 0.0 for c in cards)

        self._set_name_lbl.setText(f"{set_name}  ({set_code.upper()})")
        self._value_lbl.setText(
            f"{total_copies} copies · {distinct} distinct · "
            f"Collection value: {format_price(total_value)}"
        )

        # Render cards immediately, then fetch Scryfall metadata async
        self._render_card_table(cards)

        # Fetch Scryfall set info for completion data
        try:
            set_info = await scryfall.get_set_info(set_code)
        except Exception:
            set_info = None

        if set_info:
            total_in_set = set_info.get("card_count")
            set_type = set_info.get("set_type", "")
            released = set_info.get("released_at", "")
            meta_parts = []
            if set_type:
                meta_parts.append(set_type.replace("_", " ").title())
            if released:
                meta_parts.append(f"Released {released}")
            self._set_meta_lbl.setText("  ·  ".join(meta_parts))

            if total_in_set and total_in_set > 0:
                pct = round(distinct / total_in_set * 100, 1)
                self._owned_lbl.setText(
                    f"{distinct} / {total_in_set} distinct cards owned"
                )
                self._pct_lbl.setText(f"{pct}%")
                self._progress.setValue(int(pct))
                self._progress.setVisible(True)
            else:
                self._owned_lbl.setText(f"{distinct} distinct cards owned")
        else:
            self._owned_lbl.setText(f"{distinct} distinct cards owned")

    def _render_card_table(self, cards: list[dict]):
        """Populate the card table, sorted by collector number."""
        self._card_table.blockSignals(True)
        self._card_table.setSortingEnabled(False)
        self._card_table.setRowCount(0)
        self._card_table.setRowCount(len(cards))

        for row_idx, c in enumerate(cards):
            rarity = (c.get("rarity") or "common").lower()
            color = QColor(RARITY_COLORS.get(rarity, "#aaaaaa"))

            # Collector number — numeric sort key stored in UserRole
            cn_raw = c.get("collector_number") or ""
            try:
                cn_num = int(cn_raw)
            except (ValueError, TypeError):
                cn_num = 9999
            cn_item = _SortableItem(cn_raw)
            cn_item.setData(Qt.ItemDataRole.UserRole, cn_num)
            cn_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            name_item = QTableWidgetItem(display_name(c))
            name_item.setForeground(color)

            rarity_item = _SortableItem(rarity.capitalize())
            rarity_item.setForeground(color)
            rarity_item.setData(Qt.ItemDataRole.UserRole, _rarity_order(rarity))

            lang_item = QTableWidgetItem(c.get("language") or "en")
            cond_item = QTableWidgetItem(c.get("condition") or "—")
            foil_item = QTableWidgetItem("✨" if c.get("foil") else "")
            foil_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            price_item = _num_item(c.get("price_eur"))

            for col, item in enumerate((
                cn_item, name_item, rarity_item, lang_item,
                cond_item, foil_item, price_item,
            )):
                self._card_table.setItem(row_idx, col, item)

        self._card_table.setSortingEnabled(True)
        # Default sort: collector number ascending
        self._card_table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self._card_table.blockSignals(False)
