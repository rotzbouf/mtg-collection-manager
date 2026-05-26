"""Overcount tab — overcounted cards, sell candidates, and bundle builder."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

_log = logging.getLogger(__name__)


def _bg(coro):
    """Schedule a coroutine as a fire-and-forget task with error logging."""
    task = asyncio.ensure_future(coro)
    task.add_done_callback(
        lambda f: _log.error("Background task failed: %s", f.exception())
        if not f.cancelled() and f.exception() else None
    )
    return task

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QSpinBox, QDoubleSpinBox,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QDialog,
    QDialogButtonBox, QFormLayout, QComboBox, QMessageBox,
    QGroupBox, QTabWidget, QLineEdit, QProgressBar, QFrame, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from qasync import asyncSlot

from desktop.utils import lang_flag, format_price, display_name, RARITY_COLORS
from desktop.widgets.card_detail import CardDetailPanel

_OC_COLS          = ["Name / ID", "Set", "Cond", "Foil", "Lang", "Container", "Price (EUR)"]
_OC_COLS_CONTAINER = ["Name", "Set", "Cond", "Foil", "Lang", "×", "Price (EUR)"]
_SELL_COLS   = ["Name", "Set", "Rarity", "Foil", "Cond", "Lang", "Container", "Price (EUR)"]
_BUNDLE_COLS = ["Name", "Set", "Rarity", "Lang", "Container", "Price (EUR)"]

_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_CARD_ROLE  = Qt.ItemDataRole.UserRole + 1


def _rarity_color(rarity: str) -> QColor:
    return QColor(RARITY_COLORS.get((rarity or "").lower(), "#aaaaaa"))


class OvercountWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._threshold   = 4
        self._oc_groups:   list[dict] = []
        self._oc_view:     str = "card"   # "card" | "container"
        self._containers: list[dict] = []
        self._bundle_cards:   list[dict]       = []
        self._bundle_chunks:  list[list[dict]] = []  # non-empty in multi-bundle mode
        self._staged_bundles: list[dict]       = []  # {"name": str, "chunks": list[list[dict]]}
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load)
        QTimer.singleShot(0, self._load_containers)

    def refresh(self):
        self._load()
        self._load_containers()

    # ------------------------------------------------------------------ #
    # Top-level UI                                                          #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overcount_tab(),  "Overcounted")
        self._tabs.addTab(self._build_sell_tab(),       "Sell Candidates")
        self._tabs.addTab(self._build_bundle_tab(),     "Bundle Builder")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self._tabs)

    def _on_tab_changed(self, index: int):
        if index == 1:
            _bg(self._load_sell_candidates())
        elif index == 2:
            _bg(self._load_bundle_sets())

    # ================================================================== #
    # Tab 0 — Overcounted                                                  #
    # ================================================================== #

    def _build_overcount_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Overcounted Cards</b>"))
        toolbar.addStretch()
        toolbar.addWidget(QLabel("View:"))
        self._view_combo = QComboBox()
        self._view_combo.addItem("By Card", "card")
        self._view_combo.addItem("By Container", "container")
        self._view_combo.setFixedWidth(130)
        toolbar.addWidget(self._view_combo)
        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("Threshold ≥"))
        self._spin = QSpinBox()
        self._spin.setRange(2, 99)
        self._spin.setValue(self._threshold)
        self._spin.setFixedWidth(60)
        toolbar.addWidget(self._spin)
        self._refresh_btn = QPushButton("Refresh")
        toolbar.addWidget(self._refresh_btn)
        root.addLayout(toolbar)

        self._oc_status = QLabel("")
        self._oc_status.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(self._oc_status)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(len(_OC_COLS))
        self._tree.setHeaderLabels(_OC_COLS)
        self._tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setAlternatingRowColors(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr = self._tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_OC_COLS)):
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
        self._view_combo.currentIndexChanged.connect(self._on_view_changed)
        self._tree.currentItemChanged.connect(self._on_oc_item_selected)
        self._tree.customContextMenuRequested.connect(self._on_oc_context_menu)
        return w

    def _on_threshold_changed(self, value: int):
        self._threshold = value
        self._load()

    def _on_view_changed(self, _idx: int):
        self._oc_view = self._view_combo.currentData()
        self._render_overcount()

    @asyncSlot()
    async def _load(self):
        import core.config as cfg
        from desktop.db import db
        excluded = cfg.load().get("overcount_excluded_types", [])
        self._oc_groups = await db.get_overcount_cards(
            threshold=self._threshold, excluded_types=excluded
        )
        self._render_overcount()

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db
        try:
            self._containers = await db.list_containers()
        except Exception:
            pass

    def _populate_overcount(self, groups: list[dict]):
        """Backward-compat entry point (caches and renders)."""
        self._oc_groups = groups
        self._render_overcount()

    def _render_overcount(self):
        if self._oc_view == "container":
            self._render_by_container(self._oc_groups)
        else:
            self._render_by_card(self._oc_groups)

    # ── By-Card view (original) ────────────────────────────────────────

    def _render_by_card(self, groups: list[dict]):
        self._tree.setHeaderLabels(_OC_COLS)
        self._tree.clear()
        self._detail.clear()
        if not groups:
            self._oc_status.setText(f"No cards with {self._threshold}+ copies.")
            return
        total = sum(g["total"] for g in groups)
        self._oc_status.setText(f"{len(groups)} unique card(s)  ·  {total} total copies")

        for group in groups:
            name_en = group.get("name_en") or ""
            printed = group.get("printed_name") or group.get("name_de") or ""
            disp    = f"{printed}  ({name_en})" if printed and printed != name_en else name_en
            cnt     = group["total"]

            parent = QTreeWidgetItem([f"  {disp}  ×{cnt}", "", "", "", "", "", ""])
            parent.setExpanded(True)
            f = parent.font(0); f.setBold(True); parent.setFont(0, f)
            parent.setBackground(0, QColor("#1e2a3a"))
            parent.setForeground(0, QColor("#7eb8f7"))

            for entry in group["entries"]:
                set_info = (
                    f"{(entry.get('set_code') or '').upper()} "
                    f"#{entry.get('collector_number') or ''}"
                )
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
                child.setData(0, _CARD_ROLE,  entry)
                parent.addChild(child)
            self._tree.addTopLevelItem(parent)

    # ── By-Container view (new) ────────────────────────────────────────

    def _render_by_container(self, groups: list[dict]):
        from collections import defaultdict

        self._tree.setHeaderLabels(_OC_COLS_CONTAINER)
        self._tree.clear()
        self._detail.clear()
        if not groups:
            self._oc_status.setText(f"No cards with {self._threshold}+ copies.")
            return

        # Flatten all entries, tag each with its display name and per-card copy count
        cont_map: dict[tuple, list[tuple[dict, str, int]]] = defaultdict(list)
        for group in groups:
            name_en = group.get("name_en") or ""
            printed = group.get("printed_name") or group.get("name_de") or ""
            disp    = f"{printed}  ({name_en})" if printed and printed != name_en else name_en
            cnt     = group["total"]   # total copies of this card across all containers
            for entry in group["entries"]:
                key = (
                    entry.get("container_id") or 0,
                    entry.get("container_name") or "— No container —",
                )
                cont_map[key].append((entry, disp, cnt))

        total_entries = sum(len(v) for v in cont_map.values())
        self._oc_status.setText(
            f"{total_entries} copies  ·  {len(cont_map)} container(s)"
        )

        for (cont_id, cont_name), items in sorted(
            cont_map.items(), key=lambda kv: kv[0][1].lower()
        ):
            cont_total = sum((e.get("price_eur") or 0) for e, _, _ in items)
            header_txt = f"  📦 {cont_name}  ({len(items)} cards · {format_price(cont_total)})"

            parent = QTreeWidgetItem([header_txt, "", "", "", "", "", ""])
            parent.setExpanded(True)
            f = parent.font(0); f.setBold(True); parent.setFont(0, f)
            parent.setBackground(0, QColor("#1e2a3a"))
            parent.setForeground(0, QColor("#7eb8f7"))

            # Sort children: highest price first, then alphabetically
            for entry, card_disp, card_total in sorted(
                items,
                key=lambda t: (-(t[0].get("price_eur") or 0), t[1].lower()),
            ):
                set_info = (
                    f"{(entry.get('set_code') or '').upper()} "
                    f"#{entry.get('collector_number') or ''}"
                )
                child = QTreeWidgetItem([
                    f"    {card_disp}",
                    set_info,
                    entry.get("condition") or "",
                    "★" if entry.get("foil") else "",
                    lang_flag(entry),
                    f"×{card_total}",   # total copies of this card name
                    format_price(entry.get("price_eur")),
                ])
                child.setData(0, _ENTRY_ROLE, entry.get("id"))
                child.setData(0, _CARD_ROLE,  entry)
                parent.addChild(child)

            self._tree.addTopLevelItem(parent)

    def _on_oc_item_selected(self, current: QTreeWidgetItem, _prev):
        if current is None or current.parent() is None:
            self._detail.clear()
            return
        entry = current.data(0, _CARD_ROLE)
        if entry:
            self._detail.set_card(entry)

    def _oc_selected_ids(self) -> list[int]:
        seen: set[int] = set()
        ids: list[int] = []
        for item in self._tree.selectedItems():
            if item.parent() is None:
                continue
            cid = item.data(0, _ENTRY_ROLE)
            if cid and cid not in seen:
                seen.add(cid); ids.append(cid)
        return ids

    def _on_oc_context_menu(self, pos):
        ids = self._oc_selected_ids()
        if not ids:
            return
        n    = len(ids)
        noun = f"{n} card{'s' if n > 1 else ''}"
        menu = QMenu(self)
        menu.addAction(f"↗ Move {noun} to container…",
                       lambda: self._on_move_to_container(ids))
        menu.addAction(f"✕ Remove {noun} from container",
                       lambda: _bg(self._do_move_cards(ids, None)))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _on_move_to_container(self, card_ids: list[int]):
        _bg(self._do_open_move_dialog(card_ids))

    async def _do_open_move_dialog(self, card_ids: list[int]):
        from desktop.db import db
        self._containers = await db.list_containers()
        dlg = _MoveToContainerDialog(self._containers, len(card_ids), parent=self,
                                     allowed_types=["overcount"])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        is_new, container_id, new_name, new_type = dlg.selected_result()
        if is_new:
            try:
                container_id = await db.create_container(new_name, type=new_type)
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Could not create container:\n{exc}")
                return
        await self._do_move_cards(card_ids, container_id)

    async def _do_move_cards(self, card_ids: list[int], container_id):
        from desktop.db import db
        try:
            await db.move_cards_to_container(card_ids, container_id)
            await self._load()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ================================================================== #
    # Tab 1 — Sell Candidates                                              #
    # ================================================================== #

    def _build_sell_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(8, 8, 8, 8)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("<b>Cards in Overcount Containers</b>"))
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Min. price (EUR):"))
        self._sell_min = QDoubleSpinBox()
        self._sell_min.setRange(0.0, 999.0)
        self._sell_min.setValue(0.30)
        self._sell_min.setSingleStep(0.10)
        self._sell_min.setDecimals(2)
        self._sell_min.setFixedWidth(80)
        toolbar.addWidget(self._sell_min)
        self._sell_refresh_btn = QPushButton("Refresh")
        toolbar.addWidget(self._sell_refresh_btn)
        root.addLayout(toolbar)

        self._sell_status = QLabel("")
        self._sell_status.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(self._sell_status)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._sell_table = QTableWidget()
        self._sell_table.setColumnCount(len(_SELL_COLS))
        self._sell_table.setHorizontalHeaderLabels(_SELL_COLS)
        self._sell_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sell_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._sell_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sell_table.setAlternatingRowColors(True)
        self._sell_table.verticalHeader().setVisible(False)
        hdr = self._sell_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_SELL_COLS)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        splitter.addWidget(self._sell_table)

        self._sell_detail = CardDetailPanel(show_buttons=False)
        self._sell_detail.setMinimumWidth(260)
        self._sell_detail.setMaximumWidth(360)
        splitter.addWidget(self._sell_detail)
        splitter.setSizes([660, 300])
        root.addWidget(splitter)

        bottom = QHBoxLayout()
        self._sell_move_btn = QPushButton("↗ Move selected to container…")
        self._sell_move_btn.setEnabled(False)
        bottom.addWidget(self._sell_move_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        self._sell_refresh_btn.clicked.connect(
            lambda: _bg(self._load_sell_candidates()))
        self._sell_min.valueChanged.connect(
            lambda _: _bg(self._load_sell_candidates()))
        self._sell_table.currentCellChanged.connect(self._on_sell_row_changed)
        self._sell_table.itemSelectionChanged.connect(self._on_sell_selection_changed)
        self._sell_move_btn.clicked.connect(self._on_sell_move)
        return w

    async def _load_sell_candidates(self):
        from desktop.db import db
        min_p = self._sell_min.value()
        cards = await db.get_cards_in_overcount_containers(
            min_price=min_p, order_by="price_desc"
        )
        self._populate_sell(cards)

    def _populate_sell(self, cards: list[dict]):
        tbl = self._sell_table
        tbl.setRowCount(0)
        tbl.setSortingEnabled(False)
        self._sell_detail.clear()

        if not cards:
            self._sell_status.setText("No cards found above the price threshold.")
            self._sell_move_btn.setEnabled(False)
            return

        total_val = sum(c.get("price_eur") or 0 for c in cards)
        self._sell_status.setText(
            f"{len(cards)} card(s)  ·  total value: {format_price(total_val)}"
        )

        tbl.setRowCount(len(cards))
        for row, card in enumerate(cards):
            rarity = (card.get("rarity") or "").lower()
            col = _rarity_color(rarity)

            def _item(text: str, align=Qt.AlignmentFlag.AlignLeft) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                return it

            name_item = _item(display_name(card))
            name_item.setData(_CARD_ROLE, card)
            tbl.setItem(row, 0, name_item)
            tbl.setItem(row, 1, _item(f"{(card.get('set_code') or '').upper()} #{card.get('collector_number') or ''}"))
            rar_item = _item(rarity.capitalize())
            rar_item.setForeground(col)
            tbl.setItem(row, 2, rar_item)
            tbl.setItem(row, 3, _item("★" if card.get("foil") else ""))
            tbl.setItem(row, 4, _item(card.get("condition") or ""))
            tbl.setItem(row, 5, _item(lang_flag(card)))
            tbl.setItem(row, 6, _item(card.get("container_name") or "—"))
            price_item = _item(format_price(card.get("price_eur")), Qt.AlignmentFlag.AlignRight)
            tbl.setItem(row, 7, price_item)

        tbl.setSortingEnabled(True)

    def _on_sell_row_changed(self, row: int, _col, _pr, _pc):
        if row < 0:
            self._sell_detail.clear()
            return
        item = self._sell_table.item(row, 0)
        if item:
            card = item.data(_CARD_ROLE)
            if card:
                self._sell_detail.set_card(card)

    def _on_sell_selection_changed(self):
        self._sell_move_btn.setEnabled(bool(self._sell_table.selectedItems()))

    def _sell_selected_cards(self) -> list[dict]:
        seen: set[int] = set()
        result: list[dict] = []
        for item in self._sell_table.selectedItems():
            if item.column() != 0:
                continue
            card = item.data(_CARD_ROLE)
            cid  = card.get("id") if card else None
            if cid and cid not in seen:
                seen.add(cid); result.append(card)
        return result

    def _on_sell_move(self):
        cards = self._sell_selected_cards()
        if not cards:
            return
        _bg(self._do_open_sell_move_dialog(cards))

    async def _do_open_sell_move_dialog(self, cards: list[dict]):
        from desktop.db import db
        self._containers = await db.list_containers()
        dlg = _MoveToContainerDialog(self._containers, len(cards), parent=self,
                                     allowed_types=["overcount"])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        is_new, container_id, new_name, new_type = dlg.selected_result()
        if is_new:
            try:
                container_id = await db.create_container(new_name, type=new_type)
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Could not create container:\n{exc}")
                return
        ids = [c["id"] for c in cards]
        await self._do_sell_move(ids, container_id)

    async def _do_sell_move(self, card_ids: list[int], container_id):
        from desktop.db import db
        try:
            await db.move_cards_to_container(card_ids, container_id)
            await self._load_sell_candidates()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    # ================================================================== #
    # Tab 2 — Bundle Builder                                               #
    # ================================================================== #

    def _build_bundle_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Preset group ────────────────────────────────────────────────
        box = QGroupBox("Preset Bundles  (from overcount containers)")
        box_layout = QVBoxLayout(box)

        opt_row = QHBoxLayout()
        self._unique_only_chk = QCheckBox("Unique card names only")
        self._unique_only_chk.setChecked(True)
        self._unique_only_chk.setToolTip(
            "When checked, only one copy of each card name is included in the bundle.\n"
            "The best copy is kept based on the current sort order."
        )
        opt_row.addWidget(self._unique_only_chk)
        opt_row.addSpacing(20)
        opt_row.addWidget(QLabel("Language:"))
        self._bundle_lang_combo = QComboBox()
        self._bundle_lang_combo.setMinimumWidth(130)
        self._bundle_lang_combo.setToolTip(
            "Filter bundle cards to a single language.\n"
            "Only languages present in overcount containers are shown."
        )
        self._bundle_lang_combo.addItem("All languages", None)
        opt_row.addWidget(self._bundle_lang_combo)
        opt_row.addStretch()
        box_layout.addLayout(opt_row)

        row1 = QHBoxLayout()
        for label, rarities, count in [
            ("50 Commons",    ["common"],   50),
            ("100 Commons",   ["common"],   100),
            ("50 Uncommons",  ["uncommon"], 50),
            ("100 Uncommons", ["uncommon"], 100),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _chk, r=rarities, n=count: _bg(
                    self._preview_bundle(rarities=r, max_count=n, order="price_asc")
                )
            )
            row1.addWidget(btn)
        row1.addStretch()
        box_layout.addLayout(row1)

        row2 = QHBoxLayout()
        rares_btn = QPushButton("All Rares & Mythics")
        rares_btn.clicked.connect(
            lambda: _bg(
                self._preview_bundle(rarities=["rare", "mythic"], order="price_desc")
            )
        )
        row2.addWidget(rares_btn)

        row2.addSpacing(16)
        row2.addWidget(QLabel("By set:"))
        self._bundle_set_combo = QComboBox()
        self._bundle_set_combo.setMinimumWidth(180)
        row2.addWidget(self._bundle_set_combo)
        self._bundle_set_count = QSpinBox()
        self._bundle_set_count.setRange(1, 9999)
        self._bundle_set_count.setValue(50)
        self._bundle_set_count.setFixedWidth(70)
        self._bundle_set_count.setPrefix("×")
        row2.addWidget(self._bundle_set_count)
        by_set_btn = QPushButton("Preview")
        by_set_btn.clicked.connect(
            lambda: _bg(self._preview_set_bundle())
        )
        row2.addWidget(by_set_btn)
        row2.addStretch()
        box_layout.addLayout(row2)
        root.addWidget(box)

        # ── Preview ─────────────────────────────────────────────────────
        self._bundle_status = QLabel("Select a preset to preview the bundle.")
        self._bundle_status.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(self._bundle_status)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._bundle_tree = QTreeWidget()
        self._bundle_tree.setColumnCount(len(_BUNDLE_COLS))
        self._bundle_tree.setHeaderLabels(_BUNDLE_COLS)
        self._bundle_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._bundle_tree.setAlternatingRowColors(True)
        self._bundle_tree.setUniformRowHeights(True)
        hdr = self._bundle_tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(_BUNDLE_COLS)):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._bundle_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        splitter.addWidget(self._bundle_tree)

        self._bundle_detail = CardDetailPanel(show_buttons=False)
        self._bundle_detail.setMinimumWidth(260)
        self._bundle_detail.setMaximumWidth(360)
        splitter.addWidget(self._bundle_detail)
        splitter.setSizes([660, 300])
        root.addWidget(splitter)

        # ── Bottom bar ──────────────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Bundle name:"))
        self._bundle_name = QLineEdit()
        self._bundle_name.setPlaceholderText("e.g. Commons Bulk #1")
        self._bundle_name.setMinimumWidth(220)
        bottom.addWidget(self._bundle_name)
        bottom.addSpacing(6)

        _btn_style = (
            "QPushButton {{ background-color: {bg}; border: 1px solid {br}; padding: 5px 12px; }}"
            "QPushButton:hover {{ background-color: {hv}; }}"
            "QPushButton:disabled {{ color: #555; border-color: #333; }}"
        )
        self._bundle_add_btn = QPushButton("＋  Add to Queue")
        self._bundle_add_btn.setEnabled(False)
        self._bundle_add_btn.setStyleSheet(
            _btn_style.format(bg="#1e2a4a", br="#4a6a9a", hv="#2a3a6a")
        )
        bottom.addWidget(self._bundle_add_btn)
        bottom.addSpacing(6)

        self._bundle_create_all_btn = QPushButton("✓  Create All (0)")
        self._bundle_create_all_btn.setEnabled(False)
        self._bundle_create_all_btn.setStyleSheet(
            _btn_style.format(bg="#1e3a1e", br="#4a8a4a", hv="#2a5a2a")
        )
        bottom.addWidget(self._bundle_create_all_btn)
        bottom.addSpacing(6)

        self._bundle_clear_btn = QPushButton("✗  Clear Queue")
        self._bundle_clear_btn.setEnabled(False)
        self._bundle_clear_btn.setStyleSheet(
            _btn_style.format(bg="#3a1e1e", br="#8a4a4a", hv="#5a2a2a")
        )
        bottom.addWidget(self._bundle_clear_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        self._bundle_tree.currentItemChanged.connect(self._on_bundle_item_selected)
        self._bundle_tree.customContextMenuRequested.connect(self._on_bundle_context_menu)
        self._bundle_add_btn.clicked.connect(self._add_to_queue)
        self._bundle_create_all_btn.clicked.connect(
            lambda: _bg(self._create_all_queued())
        )
        self._bundle_clear_btn.clicked.connect(self._clear_queue)
        return w

    async def _load_bundle_sets(self):
        from desktop.db import db
        sets = await db.get_overcount_container_sets()
        self._bundle_set_combo.clear()
        self._bundle_set_combo.addItem("— select set —", None)
        for s in sets:
            label = f"{s['set_name']} ({s['set_code'].upper()})  ×{s['card_count']}"
            self._bundle_set_combo.addItem(label, s["set_code"])

        # Populate the language filter — only languages actually in overcount containers
        langs = await db.get_overcount_container_languages()
        current_lang = self._bundle_lang_combo.currentData()
        self._bundle_lang_combo.blockSignals(True)
        self._bundle_lang_combo.clear()
        self._bundle_lang_combo.addItem("All languages", None)
        for lang in langs:
            flag = lang_flag({"language": lang})
            self._bundle_lang_combo.addItem(f"{flag} {lang.upper()}", lang)
        # Restore previous selection if still available
        for i in range(self._bundle_lang_combo.count()):
            if self._bundle_lang_combo.itemData(i) == current_lang:
                self._bundle_lang_combo.setCurrentIndex(i)
                break
        self._bundle_lang_combo.blockSignals(False)

    async def _preview_bundle(
        self,
        rarities: list[str] | None = None,
        max_count: int | None = None,
        set_codes: list[str] | None = None,
        order: str = "price_asc",
    ):
        from desktop.db import db
        unique_only = self._unique_only_chk.isChecked()

        # Read language filter from the dropdown (None = all languages)
        selected_lang = self._bundle_lang_combo.currentData()
        languages = [selected_lang] if selected_lang else None

        # Fetch the full available pool — we need everything to divide into as
        # many bundles as possible.  Without max_count fall back to a safe cap.
        fetch_limit = 50_000 if max_count else (5000 if unique_only else 2000)

        cards = await db.get_cards_in_overcount_containers(
            rarities=rarities,
            set_codes=set_codes,
            languages=languages,
            order_by=order,
            limit=fetch_limit,
        )

        duplicates_excluded = 0
        if unique_only:
            seen_names: set[str] = set()
            unique_cards: list[dict] = []
            for card in cards:
                name = (card.get("name_en") or "").strip().lower()
                if not name or name not in seen_names:
                    unique_cards.append(card)
                    if name:
                        seen_names.add(name)
                else:
                    duplicates_excluded += 1
            cards = unique_cards

        # Split into chunks of max_count — each chunk becomes one bundle
        if max_count:
            chunks: list[list[dict]] = [
                cards[i:i + max_count] for i in range(0, max(len(cards), 1), max_count)
            ]
        else:
            chunks = [cards]

        self._bundle_cards  = cards
        self._bundle_chunks = chunks
        self._populate_bundle_preview(chunks, duplicates_excluded=duplicates_excluded)

        # Auto-suggest a name
        if not self._bundle_name.text().strip():
            if rarities and len(rarities) == 1:
                label = f"{max_count or 'All'} {rarities[0].capitalize()}s Bulk"
            elif rarities == ["rare", "mythic"]:
                label = "Rares & Mythics"
            elif set_codes and len(set_codes) == 1:
                label = f"Set Bundle {set_codes[0].upper()}"
            else:
                label = "Bundle"
            self._bundle_name.setText(label)

    async def _preview_set_bundle(self):
        set_code = self._bundle_set_combo.currentData()
        if not set_code:
            return
        count = self._bundle_set_count.value()
        set_name = self._bundle_set_combo.currentText().split("(")[0].strip()
        await self._preview_bundle(
            set_codes=[set_code], max_count=count, order="set"
        )
        if not self._bundle_name.text().strip():
            self._bundle_name.setText(f"{set_name} Bundle")

    def _populate_bundle_preview(self, chunks: list[list[dict]], duplicates_excluded: int = 0):
        self._bundle_tree.clear()
        self._bundle_detail.clear()
        self._bundle_add_btn.setEnabled(False)

        all_cards = [c for chunk in chunks for c in chunk]
        if not all_cards:
            self._bundle_status.setText("No cards match this preset in overcount containers.")
            return

        total_val  = sum(c.get("price_eur") or 0 for c in all_cards)
        dup_note   = (
            f"  ·  {duplicates_excluded} duplicate name{'s' if duplicates_excluded != 1 else ''} excluded"
            if duplicates_excluded else ""
        )
        rarity_order = {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3}

        def _add_card_child(parent_item: QTreeWidgetItem, card: dict):
            rarity = (card.get("rarity") or "unknown").lower()
            col    = _rarity_color(rarity)
            child  = QTreeWidgetItem([
                f"    {display_name(card)}",
                f"{(card.get('set_code') or '').upper()} #{card.get('collector_number') or ''}",
                rarity.capitalize(),
                lang_flag(card),
                card.get("container_name") or "—",
                format_price(card.get("price_eur")),
            ])
            child.setForeground(2, col)
            child.setData(0, _CARD_ROLE, card)
            child.setData(0, _ENTRY_ROLE, card.get("id"))
            parent_item.addChild(child)

        if len(chunks) > 1:
            # ── Multi-bundle: one top-level node per bundle ───────────────
            for idx, chunk in enumerate(chunks, 1):
                chunk_val = sum(c.get("price_eur") or 0 for c in chunk)
                node = QTreeWidgetItem([
                    f"  Bundle #{idx}  ×{len(chunk)}",
                    "", "", "", "",
                    format_price(chunk_val),
                ])
                node.setExpanded(idx == 1)   # expand first bundle only
                f = node.font(0); f.setBold(True); node.setFont(0, f)
                node.setForeground(0, QColor("#7eb8f7"))
                for card in chunk:
                    _add_card_child(node, card)
                self._bundle_tree.addTopLevelItem(node)

            self._bundle_status.setText(
                f"{len(chunks)} bundle(s)  ·  {len(all_cards)} card(s) total  ·  "
                f"total value: {format_price(total_val)}{dup_note}"
            )
        else:
            # ── Single bundle: group by language (original layout) ─────────
            cards = chunks[0]
            lang_groups: dict[str, list[dict]] = {}
            for card in cards:
                lang = (card.get("language") or "en").lower()
                lang_groups.setdefault(lang, []).append(card)

            for lang in sorted(lang_groups):
                lang_groups[lang].sort(
                    key=lambda c: (rarity_order.get((c.get("rarity") or "").lower(), 9),
                                   -(c.get("price_eur") or 0))
                )

            for lang in sorted(lang_groups):
                grp     = lang_groups[lang]
                grp_val = sum(c.get("price_eur") or 0 for c in grp)
                flag    = lang_flag({"language": lang})
                node = QTreeWidgetItem([
                    f"  {flag} {lang.upper()}  ×{len(grp)}",
                    "", "", "", "",
                    format_price(grp_val),
                ])
                node.setExpanded(True)
                f = node.font(0); f.setBold(True); node.setFont(0, f)
                node.setForeground(0, QColor("#7eb8f7"))
                for card in grp:
                    _add_card_child(node, card)
                self._bundle_tree.addTopLevelItem(node)

            self._bundle_status.setText(
                f"{len(cards)} card(s)  ·  {len(lang_groups)} language(s)  ·  "
                f"total value: {format_price(total_val)}{dup_note}"
            )

        self._bundle_add_btn.setEnabled(True)

    def _on_bundle_item_selected(self, current: QTreeWidgetItem, _prev):
        if current is None or current.parent() is None:
            self._bundle_detail.clear()
            return
        card = current.data(0, _CARD_ROLE)
        if card:
            self._bundle_detail.set_card(card)

    def _add_to_queue(self):
        """Stage the current preview bundle into the queue."""
        if not self._bundle_chunks:
            return
        name = self._bundle_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Bundle name required",
                                "Please enter a name for the bundle container.")
            return
        self._staged_bundles.append({"name": name, "chunks": list(self._bundle_chunks)})
        self._bundle_name.clear()
        self._bundle_chunks = []
        self._bundle_cards  = []
        self._bundle_add_btn.setEnabled(False)
        self._render_staged_queue()

    def _render_staged_queue(self):
        """Redraw the bundle tree to show all staged bundles."""
        self._bundle_tree.clear()
        self._bundle_detail.clear()

        if not self._staged_bundles:
            self._bundle_status.setText("Queue is empty. Select a preset to preview a bundle.")
            self._bundle_create_all_btn.setEnabled(False)
            self._bundle_create_all_btn.setText("✓  Create All (0)")
            self._bundle_clear_btn.setEnabled(False)
            return

        # Count how many containers will be created in total
        total_containers = 0
        for s in self._staged_bundles:
            chunks = s["chunks"]
            if len(chunks) > 1:
                total_containers += len(chunks)
            else:
                langs = {(c.get("language") or "en").lower() for c in chunks[0]}
                total_containers += len(langs)

        total_cards = sum(
            sum(len(ch) for ch in s["chunks"]) for s in self._staged_bundles
        )
        self._bundle_status.setText(
            f"{len(self._staged_bundles)} staged bundle group(s)  ·  "
            f"{total_containers} container(s) will be created  ·  "
            f"{total_cards} card(s) total  —  right-click a group to remove it"
        )

        def _make_card_child(card: dict) -> QTreeWidgetItem:
            rarity = (card.get("rarity") or "unknown").lower()
            it = QTreeWidgetItem([
                f"    {display_name(card)}",
                f"{(card.get('set_code') or '').upper()} #{card.get('collector_number') or ''}",
                rarity.capitalize(),
                lang_flag(card),
                card.get("container_name") or "—",
                format_price(card.get("price_eur")),
            ])
            it.setForeground(2, _rarity_color(rarity))
            it.setData(0, _CARD_ROLE,  card)
            it.setData(0, _ENTRY_ROLE, card.get("id"))
            return it

        for staged in self._staged_bundles:
            name   = staged["name"]
            chunks = staged["chunks"]
            all_cards = [c for ch in chunks for c in ch]
            total_val = sum(c.get("price_eur") or 0 for c in all_cards)

            top = QTreeWidgetItem([
                f"  📦 {name}  ×{len(all_cards)}",
                "", "", "", "",
                format_price(total_val),
            ])
            f = top.font(0); f.setBold(True); top.setFont(0, f)
            top.setForeground(0, QColor("#f0c060"))  # gold = staged/pending
            top.setExpanded(False)

            if len(chunks) > 1:
                for idx, chunk in enumerate(chunks, 1):
                    chunk_val = sum(c.get("price_eur") or 0 for c in chunk)
                    sub = QTreeWidgetItem([
                        f"    Bundle #{idx}  ×{len(chunk)}",
                        "", "", "", "",
                        format_price(chunk_val),
                    ])
                    f2 = sub.font(0); f2.setBold(True); sub.setFont(0, f2)
                    sub.setForeground(0, QColor("#7eb8f7"))
                    for card in chunk:
                        sub.addChild(_make_card_child(card))
                    top.addChild(sub)
            else:
                lang_groups: dict[str, list[dict]] = {}
                for card in chunks[0]:
                    lang = (card.get("language") or "en").lower()
                    lang_groups.setdefault(lang, []).append(card)
                for lang in sorted(lang_groups):
                    grp     = lang_groups[lang]
                    grp_val = sum(c.get("price_eur") or 0 for c in grp)
                    flag    = lang_flag({"language": lang})
                    sub = QTreeWidgetItem([
                        f"    {flag} {lang.upper()}  ×{len(grp)}",
                        "", "", "", "",
                        format_price(grp_val),
                    ])
                    sub.setForeground(0, QColor("#7eb8f7"))
                    for card in grp:
                        sub.addChild(_make_card_child(card))
                    top.addChild(sub)

            self._bundle_tree.addTopLevelItem(top)

        self._bundle_create_all_btn.setEnabled(True)
        self._bundle_create_all_btn.setText(f"✓  Create All ({len(self._staged_bundles)})")
        self._bundle_clear_btn.setEnabled(True)

    def _clear_queue(self):
        self._staged_bundles.clear()
        self._bundle_chunks = []
        self._bundle_cards  = []
        self._bundle_add_btn.setEnabled(False)
        self._bundle_tree.clear()
        self._bundle_detail.clear()
        self._bundle_status.setText("Queue cleared. Select a preset to preview a bundle.")
        self._bundle_create_all_btn.setEnabled(False)
        self._bundle_create_all_btn.setText("✓  Create All (0)")
        self._bundle_clear_btn.setEnabled(False)

    def _on_bundle_context_menu(self, pos):
        item = self._bundle_tree.itemAt(pos)
        if item is None or item.parent() is not None:
            return   # only top-level staged-group nodes are removable
        if not self._staged_bundles:
            return   # tree is showing a preview, not the queue
        idx = self._bundle_tree.indexOfTopLevelItem(item)
        if idx < 0 or idx >= len(self._staged_bundles):
            return
        staged_name = self._staged_bundles[idx]["name"]
        menu = QMenu(self)
        menu.addAction(
            f'✕  Remove "{staged_name}" from queue',
            lambda: self._remove_from_queue(idx),
        )
        menu.exec(self._bundle_tree.viewport().mapToGlobal(pos))

    def _remove_from_queue(self, idx: int):
        if 0 <= idx < len(self._staged_bundles):
            self._staged_bundles.pop(idx)
            self._render_staged_queue()

    async def _create_all_queued(self):
        if not self._staged_bundles:
            return

        from desktop.db import db
        try:
            all_created: list[str] = []
            total_moved = 0

            for staged in self._staged_bundles:
                name   = staged["name"]
                chunks = staged["chunks"]

                if len(chunks) > 1:
                    # One container per chunk, named "Name #1", "Name #2", …
                    for idx, chunk in enumerate(chunks, 1):
                        container_name = f"{name} #{idx}"
                        container_id   = await db.create_container(
                            container_name, description="Bundle", type="box"
                        )
                        card_ids = [c["id"] for c in chunk if c.get("id")]
                        moved    = await db.move_cards_to_container(card_ids, container_id)
                        total_moved += moved
                        all_created.append(f"  • {container_name}  ({moved} cards)")
                else:
                    # Single bundle: per-language split → "Name (DE)", "Name (EN)", …
                    lang_groups: dict[str, list[dict]] = {}
                    for card in chunks[0]:
                        lang = (card.get("language") or "en").lower()
                        lang_groups.setdefault(lang, []).append(card)
                    for lang in sorted(lang_groups):
                        container_name = f"{name} ({lang.upper()})"
                        container_id   = await db.create_container(
                            container_name, description="Bundle", type="box"
                        )
                        card_ids = [c["id"] for c in lang_groups[lang] if c.get("id")]
                        moved    = await db.move_cards_to_container(card_ids, container_id)
                        total_moved += moved
                        all_created.append(f"  • {container_name}  ({moved} cards)")

            QMessageBox.information(
                self, "Bundles created",
                f"{len(all_created)} container(s) created — {total_moved} card(s) total:\n\n"
                + "\n".join(all_created),
            )

            # Reset everything
            self._staged_bundles.clear()
            self._bundle_chunks = []
            self._bundle_cards  = []
            self._bundle_name.clear()
            self._bundle_tree.clear()
            self._bundle_detail.clear()
            self._bundle_status.setText("All bundles created. Select a preset to build more.")
            self._bundle_add_btn.setEnabled(False)
            self._bundle_create_all_btn.setEnabled(False)
            self._bundle_create_all_btn.setText("✓  Create All (0)")
            self._bundle_clear_btn.setEnabled(False)
            await self._load_containers_async()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    async def _load_containers_async(self):
        from desktop.db import db
        try:
            self._containers = await db.list_containers()
        except Exception:
            pass


# ------------------------------------------------------------------ #
# Shared dialogs                                                        #
# ------------------------------------------------------------------ #

class _MoveToContainerDialog(QDialog):
    def __init__(self, containers: list[dict], card_count: int, parent=None,
                 allowed_types: list[str] | None = None):
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
            if allowed_types and c.get("type") not in allowed_types:
                continue
            self._combo.addItem(f"{c['name']}  [{c.get('type', '')}]", c["id"])
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
        type_choices = (
            allowed_types
            if allowed_types
            else cfg.load().get("container_types", cfg.BUILTIN_TYPES)
        )
        self._new_type_cb.addItems(type_choices)
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
