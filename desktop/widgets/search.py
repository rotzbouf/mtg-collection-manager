"""Search tab — advanced filter search across the collection (extensible draft)."""
from __future__ import annotations

from typing import Optional

import asyncio

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QLineEdit, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QGroupBox, QScrollArea, QFrame, QSpinBox, QDoubleSpinBox,
    QSizePolicy, QMenu, QDialog, QDialogButtonBox, QFormLayout,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from qasync import asyncSlot

from core.i18n import _
from desktop.utils import CONDITIONS, display_name, lang_flag, format_price
from desktop.widgets.card_detail import CardDetailPanel

_LANGUAGES = [
    ("", "Any"),
    ("en", "English"), ("de", "German"), ("fr", "French"),
    ("it", "Italian"), ("es", "Spanish"), ("pt", "Portuguese"),
    ("ja", "Japanese"), ("ko", "Korean"), ("ru", "Russian"),
    ("zhs", "Simplified Chinese"), ("zht", "Traditional Chinese"),
]

_RARITIES = [
    ("common",   "C", "#aaaaaa"),
    ("uncommon", "U", "#70b0c0"),
    ("rare",     "R", "#d4af37"),
    ("mythic",   "M", "#e07030"),
]

_COLORS = [
    ("W", "W", "#f5f5dc", "#333"),
    ("U", "U", "#4169e1", "#fff"),
    ("B", "B", "#2c2c2c", "#ccc"),
    ("R", "R", "#c0392b", "#fff"),
    ("G", "G", "#27ae60", "#fff"),
    ("C", "∅", "#888888", "#fff"),
]

_RESULT_COLS = ["#", "Name", "Set", "CN", "Type", "Cond", "Foil", "Lang", "Ctr", "€"]

def _result_cols():
    return [_("#"), _("Name"), _("Set"), _("CN"), _("Type"), _("Cond"), _("Foil"), _("Lang"), _("Ctr"), _("€")]


class SearchWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[dict] = []
        self._containers: list[dict] = []
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_containers)

    def refresh(self):
        self._load_containers()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self._build_filter_pane())

        # Results + detail
        inner = QSplitter(Qt.Orientation.Horizontal)
        inner.addWidget(self._build_results_pane())
        inner.addWidget(self._build_detail_pane())
        inner.setSizes([680, 360])

        outer.addWidget(inner)
        outer.setSizes([280, 1040])

        root.addWidget(outer)

    # ---- Filter pane ------------------------------------------------- #

    def _build_filter_pane(self) -> QWidget:
        container = QWidget()
        container.setMaximumWidth(310)
        outer_layout = QVBoxLayout(container)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Scrollable filter area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(8)

        layout.addWidget(QLabel(_("<h2>Search</h2>")))

        # ── Text filters ──────────────────────────────────────────────
        text_box = QGroupBox(_("Text"))
        text_layout = QVBoxLayout(text_box)
        text_layout.setSpacing(4)

        text_layout.addWidget(QLabel(_("Name:")))
        self._f_name = QLineEdit()
        self._f_name.setPlaceholderText(_("full or partial…"))
        self._f_name.setClearButtonEnabled(True)
        text_layout.addWidget(self._f_name)

        text_layout.addWidget(QLabel(_("Type line:")))
        self._f_type = QLineEdit()
        self._f_type.setPlaceholderText(_("e.g. Creature, Instant…"))
        self._f_type.setClearButtonEnabled(True)
        text_layout.addWidget(self._f_type)

        text_layout.addWidget(QLabel(_("Oracle text:")))
        self._f_oracle = QLineEdit()
        self._f_oracle.setPlaceholderText(_("contains…"))
        self._f_oracle.setClearButtonEnabled(True)
        text_layout.addWidget(self._f_oracle)

        text_layout.addWidget(QLabel(_("Set code:")))
        self._f_set = QLineEdit()
        self._f_set.setPlaceholderText(_("e.g. mh2"))
        self._f_set.setClearButtonEnabled(True)
        text_layout.addWidget(self._f_set)

        layout.addWidget(text_box)

        # ── Colors ───────────────────────────────────────────────────
        color_box = QGroupBox(_("Colors"))
        color_layout = QVBoxLayout(color_box)
        color_layout.setSpacing(4)

        pip_row = QHBoxLayout()
        pip_row.setSpacing(4)
        self._color_cbs: dict[str, QCheckBox] = {}
        for code, label, bg, fg in _COLORS:
            cb = QCheckBox(label)
            cb.setStyleSheet(
                f"QCheckBox::indicator {{ width: 22px; height: 22px; border-radius: 11px;"
                f" background: {bg}; border: 1px solid #555; }}"
                f"QCheckBox::indicator:checked {{ border: 2px solid #fff; }}"
                f"QCheckBox {{ color: {fg}; font-weight: bold; font-size: 12px; }}"
            )
            self._color_cbs[code] = cb
            pip_row.addWidget(cb)
        pip_row.addStretch()
        color_layout.addLayout(pip_row)

        self._f_colors_exclusive = QCheckBox(_("Exact (no other colors)"))
        self._f_colors_exclusive.setStyleSheet("font-size: 11px; color: #a6adc8;")
        color_layout.addWidget(self._f_colors_exclusive)

        layout.addWidget(color_box)

        # ── Card properties ──────────────────────────────────────────
        props_box = QGroupBox(_("Properties"))
        props_layout = QVBoxLayout(props_box)
        props_layout.setSpacing(6)

        props_layout.addWidget(QLabel(_("Rarity:")))
        rar_row = QHBoxLayout()
        rar_row.setSpacing(6)
        self._rarity_cbs: dict[str, QCheckBox] = {}
        for key, label, color in _RARITIES:
            cb = QCheckBox(label)
            cb.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 13px;")
            self._rarity_cbs[key] = cb
            rar_row.addWidget(cb)
        rar_row.addStretch()
        props_layout.addLayout(rar_row)

        props_layout.addWidget(QLabel(_("Mana Value:")))
        cmc_row = QHBoxLayout()
        cmc_row.setSpacing(4)
        self._f_cmc_min = QSpinBox()
        self._f_cmc_min.setRange(0, 20)
        self._f_cmc_min.setSpecialValueText("–")
        self._f_cmc_min.setValue(0)
        self._f_cmc_max = QSpinBox()
        self._f_cmc_max.setRange(0, 20)
        self._f_cmc_max.setSpecialValueText("–")
        self._f_cmc_max.setValue(0)
        cmc_row.addWidget(self._f_cmc_min)
        cmc_row.addWidget(QLabel("–"))
        cmc_row.addWidget(self._f_cmc_max)
        cmc_row.addStretch()
        props_layout.addLayout(cmc_row)

        layout.addWidget(props_box)

        # ── Collection metadata ───────────────────────────────────────
        coll_box = QGroupBox(_("Collection"))
        coll_layout = QVBoxLayout(coll_box)
        coll_layout.setSpacing(4)

        coll_layout.addWidget(QLabel(_("Condition:")))
        self._f_condition = QComboBox()
        self._f_condition.addItem(_("Any"), "")
        for c in CONDITIONS:
            self._f_condition.addItem(c, c)
        coll_layout.addWidget(self._f_condition)

        coll_layout.addWidget(QLabel(_("Language:")))
        self._f_language = QComboBox()
        for code, label in _LANGUAGES:
            self._f_language.addItem(label, code)
        coll_layout.addWidget(self._f_language)

        coll_layout.addWidget(QLabel(_("Foil:")))
        self._f_foil = QComboBox()
        self._f_foil.addItem(_("Any"), None)
        self._f_foil.addItem(_("Foil only"), 1)
        self._f_foil.addItem(_("Non-foil only"), 0)
        coll_layout.addWidget(self._f_foil)

        coll_layout.addWidget(QLabel(_("Container:")))
        self._f_container = QComboBox()
        self._f_container.addItem(_("Any"), None)
        self._f_container.addItem(_("(no container)"), -1)
        coll_layout.addWidget(self._f_container)

        self._f_commander = QCheckBox(_("Commander only"))
        coll_layout.addWidget(self._f_commander)

        layout.addWidget(coll_box)

        # ── Price ─────────────────────────────────────────────────────
        price_box = QGroupBox(_("Price (EUR)"))
        price_layout = QHBoxLayout(price_box)
        price_layout.setSpacing(4)
        price_layout.addWidget(QLabel(_("Min:")))
        self._f_price_min = QDoubleSpinBox()
        self._f_price_min.setRange(0, 9999)
        self._f_price_min.setDecimals(2)
        self._f_price_min.setSpecialValueText("–")
        self._f_price_min.setValue(0)
        self._f_price_min.setMaximumWidth(80)
        price_layout.addWidget(self._f_price_min)
        price_layout.addWidget(QLabel(_("Max:")))
        self._f_price_max = QDoubleSpinBox()
        self._f_price_max.setRange(0, 9999)
        self._f_price_max.setDecimals(2)
        self._f_price_max.setSpecialValueText("–")
        self._f_price_max.setValue(0)
        self._f_price_max.setMaximumWidth(80)
        price_layout.addWidget(self._f_price_max)
        price_layout.addStretch()
        layout.addWidget(price_box)

        layout.addStretch()
        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)

        # Action buttons (outside scroll, always visible)
        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(8, 6, 8, 8)
        btn_layout.setSpacing(6)
        self._search_btn = QPushButton(_("Search"))
        self._search_btn.setStyleSheet(
            "padding: 7px; background: #0f3460; color: white; border-radius: 3px; font-size: 13px;"
        )
        self._reset_btn = QPushButton(_("Reset"))
        self._reset_btn.setStyleSheet("padding: 7px; font-size: 13px;")
        btn_layout.addWidget(self._search_btn, stretch=1)
        btn_layout.addWidget(self._reset_btn)
        outer_layout.addWidget(btn_bar)

        # Wire signals
        self._search_btn.clicked.connect(self._on_search)
        self._reset_btn.clicked.connect(self._on_reset)
        for edit in (self._f_name, self._f_type, self._f_oracle, self._f_set):
            edit.returnPressed.connect(self._on_search)

        return container

    # ---- Results pane ------------------------------------------------ #

    def _build_results_pane(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(4)

        self._status_lbl = QLabel(_("Enter filters and press Search."))
        self._status_lbl.setStyleSheet("color: #a6adc8; font-size: 12px; padding: 0 8px;")
        layout.addWidget(self._status_lbl)

        self._table = QTableWidget(0, len(_RESULT_COLS))
        self._table.setHorizontalHeaderLabels(_result_cols())
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for col in [0, 2, 3, 5, 6, 7, 8, 9]:
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        return w

    # ---- Detail pane ------------------------------------------------- #

    def _build_detail_pane(self) -> QWidget:
        self._detail = CardDetailPanel(show_buttons=False)
        self._detail.setMinimumWidth(300)
        self._detail.setMaximumWidth(420)
        return self._detail

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db

        try:
            self._containers = await db.list_containers()
        except Exception:
            return

        current = self._f_container.currentData()
        self._f_container.blockSignals(True)
        while self._f_container.count() > 2:
            self._f_container.removeItem(2)
        for c in self._containers:
            self._f_container.addItem(c["name"], c["id"])
        for i in range(self._f_container.count()):
            if self._f_container.itemData(i) == current:
                self._f_container.setCurrentIndex(i)
                break
        self._f_container.blockSignals(False)

    # ------------------------------------------------------------------ #
    # Slots                                                                 #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_search(self):
        from desktop.db import db

        self._search_btn.setEnabled(False)
        self._status_lbl.setText(_("Searching…"))
        self._table.setRowCount(0)
        self._detail.clear()
        self._results = []

        # Collect filter values
        name       = self._f_name.text().strip()
        type_line  = self._f_type.text().strip()
        oracle     = self._f_oracle.text().strip()
        set_code   = self._f_set.text().strip()

        colors = [c for c, cb in self._color_cbs.items() if cb.isChecked()]
        colors_excl = self._f_colors_exclusive.isChecked()

        rarities = [k for k, cb in self._rarity_cbs.items() if cb.isChecked()]

        cmc_min_val = self._f_cmc_min.value()
        cmc_max_val = self._f_cmc_max.value()
        cmc_min = float(cmc_min_val) if cmc_min_val > 0 else None
        cmc_max = float(cmc_max_val) if cmc_max_val > 0 else None

        condition  = self._f_condition.currentData()
        language   = self._f_language.currentData()
        foil       = self._f_foil.currentData()
        ctr_id     = self._f_container.currentData()
        cmd_only   = self._f_commander.isChecked()

        price_min_val = self._f_price_min.value()
        price_max_val = self._f_price_max.value()
        price_min = price_min_val if price_min_val > 0 else None
        price_max = price_max_val if price_max_val > 0 else None

        try:
            cards = await db.advanced_search(
                name=name,
                type_line=type_line,
                oracle_text=oracle,
                set_code=set_code,
                colors=colors or None,
                colors_exclusive=colors_excl,
                rarities=rarities or None,
                cmc_min=cmc_min,
                cmc_max=cmc_max,
                condition=condition,
                language=language,
                foil=foil,
                container_id=ctr_id,
                commander_only=cmd_only,
                price_min=price_min,
                price_max=price_max,
            )
        except Exception as exc:
            self._status_lbl.setText(f"Error: {exc}")
            self._search_btn.setEnabled(True)
            return

        self._search_btn.setEnabled(True)
        self._results = cards

        if not cards:
            self._status_lbl.setText(_("No results found."))
            return

        self._status_lbl.setText(
            _("{count} result(s)").format(count=len(cards))
            + (" — " + _("limit reached, refine your filters") if len(cards) == 300 else "")
        )
        self._populate_table(cards)

    def _populate_table(self, cards: list[dict]):
        self._table.setRowCount(0)
        for row_idx, card in enumerate(cards):
            self._table.insertRow(row_idx)
            is_cmd = bool(card.get("is_commander"))

            def _item(text: str, cid=None, cmd=is_cmd) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if cid is not None:
                    it.setData(Qt.ItemDataRole.UserRole, cid)
                if cmd:
                    it.setBackground(QColor("#1e1600"))
                    it.setForeground(QColor("#f0c040"))
                return it

            name_text = ("👑 " if is_cmd else "") + display_name(card)
            self._table.setItem(row_idx, 0, _item(str(card.get("id", "")), card.get("id")))
            self._table.setItem(row_idx, 1, _item(name_text))
            self._table.setItem(row_idx, 2, _item((card.get("set_code") or "").upper()))
            self._table.setItem(row_idx, 3, _item(card.get("collector_number") or ""))
            self._table.setItem(row_idx, 4, _item(card.get("type_line") or ""))
            self._table.setItem(row_idx, 5, _item(card.get("condition") or ""))
            self._table.setItem(row_idx, 6, _item("★" if card.get("foil") else ""))
            self._table.setItem(row_idx, 7, _item(lang_flag(card)))
            self._table.setItem(row_idx, 8, _item(card.get("container_name") or "—"))
            self._table.setItem(row_idx, 9, _item(format_price(card.get("price_eur"), card.get("price_approx", 0))))

    def _on_row_selected(self):
        selected = self._selected_card_ids()
        if len(selected) == 1:
            card = next((c for c in self._results if c.get("id") == selected[0]), None)
            if card:
                self._detail.set_card(card)
                return
        self._detail.clear()

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

    def _on_context_menu(self, pos):
        card_ids = self._selected_card_ids()
        if not card_ids:
            return
        n = len(card_ids)
        noun = f"{n} card{'s' if n > 1 else ''}"
        menu = QMenu(self)
        menu.addAction(f"↗ Move {noun} to container…",
                       lambda: self._on_move_to_container(card_ids))
        menu.addAction(f"✕ Remove {noun} from container",
                       lambda: self._on_move_to_container(card_ids, remove=True))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_move_to_container(self, card_ids: list[int], remove: bool = False):
        if remove:
            asyncio.ensure_future(self._do_move_cards(card_ids, None))
            return
        dlg = _MoveToContainerDialog(self._containers, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            asyncio.ensure_future(self._do_move_cards(card_ids, dlg.selected_id()))

    async def _do_move_cards(self, card_ids: list[int], container_id):
        from desktop.db import db
        try:
            await db.move_cards_to_container(card_ids, container_id)
            await self._on_search()
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", str(exc))

    def _on_reset(self):
        self._f_name.clear()
        self._f_type.clear()
        self._f_oracle.clear()
        self._f_set.clear()
        for cb in self._color_cbs.values():
            cb.setChecked(False)
        self._f_colors_exclusive.setChecked(False)
        for cb in self._rarity_cbs.values():
            cb.setChecked(False)
        self._f_cmc_min.setValue(0)
        self._f_cmc_max.setValue(0)
        self._f_condition.setCurrentIndex(0)
        self._f_language.setCurrentIndex(0)
        self._f_foil.setCurrentIndex(0)
        self._f_container.setCurrentIndex(0)
        self._f_commander.setChecked(False)
        self._f_price_min.setValue(0)
        self._f_price_max.setValue(0)
        self._table.setRowCount(0)
        self._detail.clear()
        self._results = []
        self._status_lbl.setText(_("Filters reset."))


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
