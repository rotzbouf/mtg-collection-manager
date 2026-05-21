"""Overcount tab — overcounted cards, sell candidates, and bundle builder."""
from __future__ import annotations

import asyncio
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QSpinBox, QDoubleSpinBox,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMenu, QDialog,
    QDialogButtonBox, QFormLayout, QComboBox, QMessageBox,
    QGroupBox, QTabWidget, QLineEdit, QProgressBar, QFrame,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from qasync import asyncSlot

from desktop.utils import lang_flag, format_price, display_name, RARITY_COLORS
from desktop.widgets.card_detail import CardDetailPanel

_OC_COLS     = ["Name / ID", "Set", "Cond", "Foil", "Lang", "Container", "Price (EUR)"]
_SELL_COLS   = ["Name", "Set", "Rarity", "Foil", "Cond", "Lang", "Container", "Price (EUR)"]
_BUNDLE_COLS = ["Name", "Set", "Rarity", "Lang", "Price (EUR)"]

_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_CARD_ROLE  = Qt.ItemDataRole.UserRole + 1


def _rarity_color(rarity: str) -> QColor:
    return QColor(RARITY_COLORS.get((rarity or "").lower(), "#aaaaaa"))


class OvercountWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._threshold   = 4
        self._containers: list[dict] = []
        self._bundle_cards: list[dict] = []
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
            asyncio.ensure_future(self._load_sell_candidates())
        elif index == 2:
            asyncio.ensure_future(self._load_bundle_sets())

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
        self._tree.currentItemChanged.connect(self._on_oc_item_selected)
        self._tree.customContextMenuRequested.connect(self._on_oc_context_menu)
        return w

    def _on_threshold_changed(self, value: int):
        self._threshold = value
        self._load()

    @asyncSlot()
    async def _load(self):
        import core.config as cfg
        from desktop.db import db
        excluded = cfg.load().get("overcount_excluded_types", [])
        cards = await db.get_overcount_cards(threshold=self._threshold, excluded_types=excluded)
        self._populate_overcount(cards)

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db
        try:
            self._containers = await db.list_containers()
        except Exception:
            pass

    def _populate_overcount(self, groups: list[dict]):
        self._tree.clear()
        self._detail.clear()
        if not groups:
            self._oc_status.setText(f"No cards with {self._threshold}+ copies.")
            return
        total = sum(g["total"] for g in groups)
        self._oc_status.setText(f"{len(groups)} unique card(s)  ·  {total} total copies")

        for group in groups:
            name_en  = group.get("name_en") or ""
            printed  = group.get("printed_name") or group.get("name_de") or ""
            disp     = f"{printed}  ({name_en})" if printed and printed != name_en else name_en
            cnt      = group["total"]

            parent = QTreeWidgetItem([f"  {disp}  ×{cnt}", "", "", "", "", "", ""])
            parent.setExpanded(True)
            f = parent.font(0); f.setBold(True); parent.setFont(0, f)
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
                       lambda: asyncio.ensure_future(self._do_move_cards(ids, None)))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _on_move_to_container(self, card_ids: list[int]):
        asyncio.ensure_future(self._do_open_move_dialog(card_ids))

    async def _do_open_move_dialog(self, card_ids: list[int]):
        from desktop.db import db
        self._containers = await db.list_containers()
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
            lambda: asyncio.ensure_future(self._load_sell_candidates()))
        self._sell_min.valueChanged.connect(
            lambda _: asyncio.ensure_future(self._load_sell_candidates()))
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
        asyncio.ensure_future(self._do_open_sell_move_dialog(cards))

    async def _do_open_sell_move_dialog(self, cards: list[dict]):
        from desktop.db import db
        self._containers = await db.list_containers()
        dlg = _MoveToContainerDialog(self._containers, len(cards), parent=self)
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
        box = QGroupBox("Preset Bundles  (cheapest cards from overcount containers first)")
        box_layout = QVBoxLayout(box)

        row1 = QHBoxLayout()
        for label, rarities, count in [
            ("50 Commons",    ["common"],   50),
            ("100 Commons",   ["common"],   100),
            ("50 Uncommons",  ["uncommon"], 50),
            ("100 Uncommons", ["uncommon"], 100),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _chk, r=rarities, n=count: asyncio.ensure_future(
                    self._preview_bundle(rarities=r, max_count=n, order="price_asc")
                )
            )
            row1.addWidget(btn)
        row1.addStretch()
        box_layout.addLayout(row1)

        row2 = QHBoxLayout()
        rares_btn = QPushButton("All Rares & Mythics")
        rares_btn.clicked.connect(
            lambda: asyncio.ensure_future(
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
            lambda: asyncio.ensure_future(self._preview_set_bundle())
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
        self._bundle_create_btn = QPushButton("✓  Create Bundle Container")
        self._bundle_create_btn.setEnabled(False)
        self._bundle_create_btn.setStyleSheet(
            "QPushButton { background-color: #1e3a1e; border: 1px solid #4a8a4a; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #2a5a2a; }"
            "QPushButton:disabled { color: #555; border-color: #333; }"
        )
        bottom.addWidget(self._bundle_create_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        self._bundle_tree.currentItemChanged.connect(self._on_bundle_item_selected)
        self._bundle_create_btn.clicked.connect(
            lambda: asyncio.ensure_future(self._create_bundle())
        )
        return w

    async def _load_bundle_sets(self):
        from desktop.db import db
        sets = await db.get_overcount_container_sets()
        self._bundle_set_combo.clear()
        self._bundle_set_combo.addItem("— select set —", None)
        for s in sets:
            label = f"{s['set_name']} ({s['set_code'].upper()})  ×{s['card_count']}"
            self._bundle_set_combo.addItem(label, s["set_code"])

    async def _preview_bundle(
        self,
        rarities: list[str] | None = None,
        max_count: int | None = None,
        set_codes: list[str] | None = None,
        order: str = "price_asc",
    ):
        from desktop.db import db
        cards = await db.get_cards_in_overcount_containers(
            rarities=rarities,
            set_codes=set_codes,
            order_by=order,
            limit=max_count or 2000,
        )
        self._bundle_cards = cards
        self._populate_bundle_preview(cards)

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

    def _populate_bundle_preview(self, cards: list[dict]):
        self._bundle_tree.clear()
        self._bundle_detail.clear()
        self._bundle_create_btn.setEnabled(False)

        if not cards:
            self._bundle_status.setText("No cards match this preset in overcount containers.")
            return

        total_val = sum(c.get("price_eur") or 0 for c in cards)

        # Group by language, then sort within each group by rarity
        lang_groups: dict[str, list[dict]] = {}
        for card in cards:
            lang = (card.get("language") or "en").lower()
            lang_groups.setdefault(lang, []).append(card)

        rarity_order = {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3}
        for lang in sorted(lang_groups):
            lang_groups[lang].sort(
                key=lambda c: (rarity_order.get((c.get("rarity") or "").lower(), 9),
                               -(c.get("price_eur") or 0))
            )

        added = 0
        for lang in sorted(lang_groups):
            grp     = lang_groups[lang]
            grp_val = sum(c.get("price_eur") or 0 for c in grp)
            flag    = lang_flag({"language": lang})

            parent = QTreeWidgetItem([
                f"  {flag} {lang.upper()}  ×{len(grp)}",
                "",
                "",
                "",
                format_price(grp_val),
            ])
            parent.setExpanded(True)
            f = parent.font(0); f.setBold(True); parent.setFont(0, f)
            parent.setForeground(0, QColor("#7eb8f7"))

            for card in grp:
                rarity = (card.get("rarity") or "unknown").lower()
                col    = _rarity_color(rarity)
                child  = QTreeWidgetItem([
                    f"    {display_name(card)}",
                    f"{(card.get('set_code') or '').upper()} #{card.get('collector_number') or ''}",
                    rarity.capitalize(),
                    lang_flag(card),
                    format_price(card.get("price_eur")),
                ])
                child.setForeground(2, col)
                child.setData(0, _CARD_ROLE, card)
                child.setData(0, _ENTRY_ROLE, card.get("id"))
                parent.addChild(child)
                added += 1

            self._bundle_tree.addTopLevelItem(parent)

        self._bundle_status.setText(
            f"{added} card(s)  ·  {len(lang_groups)} language(s)  ·  total value: {format_price(total_val)}"
        )
        self._bundle_create_btn.setEnabled(True)

    def _on_bundle_item_selected(self, current: QTreeWidgetItem, _prev):
        if current is None or current.parent() is None:
            self._bundle_detail.clear()
            return
        card = current.data(0, _CARD_ROLE)
        if card:
            self._bundle_detail.set_card(card)

    async def _create_bundle(self):
        if not self._bundle_cards:
            return
        name = self._bundle_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Bundle name required", "Please enter a name for the bundle container.")
            return

        # Split cards by language → one container per language
        lang_groups: dict[str, list[dict]] = {}
        for card in self._bundle_cards:
            lang = (card.get("language") or "en").lower()
            lang_groups.setdefault(lang, []).append(card)

        from desktop.db import db
        try:
            total_moved = 0
            created: list[str] = []
            for lang in sorted(lang_groups):
                container_name = f"{name} ({lang.upper()})"
                container_id   = await db.create_container(container_name, description="Bundle", type="box")
                card_ids       = [c["id"] for c in lang_groups[lang] if c.get("id")]
                moved          = await db.move_cards_to_container(card_ids, container_id)
                total_moved   += moved
                created.append(f"  • {container_name}  ({moved} cards)")

            QMessageBox.information(
                self, "Bundles created",
                f"{len(created)} container(s) created — {total_moved} card(s) total:\n\n"
                + "\n".join(created),
            )
            self._bundle_cards = []
            self._bundle_name.clear()
            self._bundle_tree.clear()
            self._bundle_status.setText("Bundles created. Select a preset to build another.")
            self._bundle_create_btn.setEnabled(False)
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
