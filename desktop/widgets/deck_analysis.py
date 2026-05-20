"""Deck Analysis tab — inspect and edit decks stored as containers."""
from __future__ import annotations

import asyncio
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QMenu, QMessageBox, QFileDialog, QDialog,
    QDialogButtonBox, QListWidget, QListWidgetItem, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QAction
from qasync import asyncSlot


def _monofont() -> QFont:
    font = QFont("Monospace")
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPointSize(9)
    return font


def _type_group(card: dict) -> str:
    tl = card.get("type_line") or ""
    for token in ("Creature", "Planeswalker", "Instant", "Sorcery",
                  "Enchantment", "Artifact", "Land"):
        if token in tl:
            return token + "s"
    return "Other"


class DeckAnalysisWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._containers: list[dict] = []  # deck/commander containers
        self._cards: list[dict] = []       # all cards in current deck
        self._filtered: list[dict] = []    # after name/type filter
        self._current_container: dict | None = None
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_decks)

    def refresh(self):
        asyncio.ensure_future(self._async_load_decks())

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Header
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("<h2>Deck Analysis</h2>"))
        hdr.addStretch()
        hdr.addWidget(QLabel("Deck:"))
        self._deck_cb = QComboBox()
        self._deck_cb.setMinimumWidth(280)
        hdr.addWidget(self._deck_cb)
        self._refresh_btn = QPushButton("↻")
        self._refresh_btn.setFixedWidth(28)
        self._refresh_btn.setToolTip("Reload")
        hdr.addWidget(self._refresh_btn)
        root.addLayout(hdr)

        # Stats bar
        self._stats_lbl = QLabel("")
        self._stats_lbl.setWordWrap(True)
        self._stats_lbl.setStyleSheet("color: #aaa; font-size: 11px; padding: 2px 0;")
        root.addWidget(self._stats_lbl)

        self._curve_lbl = QLabel("")
        self._curve_lbl.setFont(_monofont())
        self._curve_lbl.setStyleSheet("color: #666; font-size: 9px;")
        root.addWidget(self._curve_lbl)

        # Filters
        flt = QHBoxLayout()
        self._name_filter = QLineEdit()
        self._name_filter.setPlaceholderText("Filter by name…")
        self._name_filter.setClearButtonEnabled(True)
        flt.addWidget(self._name_filter, stretch=2)
        self._type_filter = QComboBox()
        self._type_filter.addItem("All types", None)
        for t in ("Creatures", "Instants", "Sorceries", "Enchantments",
                  "Artifacts", "Planeswalkers", "Lands", "Other"):
            self._type_filter.addItem(t, t)
        flt.addWidget(self._type_filter)
        root.addLayout(flt)

        # Card table
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["CMC", "Name", "Type", "Container", "€"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        root.addWidget(self._table, stretch=1)

        # Toolbar
        bar = QHBoxLayout()
        self._add_btn = QPushButton("+ Add from collection")
        self._add_btn.setEnabled(False)
        bar.addWidget(self._add_btn)
        self._remove_btn = QPushButton("Remove selected")
        self._remove_btn.setEnabled(False)
        bar.addWidget(self._remove_btn)
        bar.addStretch()
        self._exp_full_btn = QPushButton("Export .txt")
        self._exp_full_btn.setEnabled(False)
        bar.addWidget(self._exp_full_btn)
        self._exp_mtga_btn = QPushButton("Export MTGA")
        self._exp_mtga_btn.setEnabled(False)
        bar.addWidget(self._exp_mtga_btn)
        root.addLayout(bar)

        # Signals
        self._deck_cb.currentIndexChanged.connect(self._on_deck_changed)
        self._refresh_btn.clicked.connect(self.refresh)
        self._name_filter.textChanged.connect(self._apply_filter)
        self._type_filter.currentIndexChanged.connect(self._apply_filter)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._add_btn.clicked.connect(self._on_add_cards)
        self._remove_btn.clicked.connect(self._on_remove_selected)
        self._exp_full_btn.clicked.connect(lambda: self._on_export(mtga=False))
        self._exp_mtga_btn.clicked.connect(lambda: self._on_export(mtga=True))

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_decks(self):
        await self._async_load_decks()

    async def _async_load_decks(self):
        from desktop.db import db
        try:
            all_containers = await db.list_containers()
        except Exception:
            return
        self._containers = [
            c for c in all_containers
            if c.get("type") in ("deck", "commander")
        ]
        prev_id = self._deck_cb.currentData()
        self._deck_cb.blockSignals(True)
        self._deck_cb.clear()
        self._deck_cb.addItem("— Select a deck —", None)
        for c in self._containers:
            fmt = c.get("deck_format") or c.get("type") or ""
            label = f"{c['name']}  [{fmt}]  {c.get('card_count', 0)} cards"
            self._deck_cb.addItem(label, c["id"])
        # Restore selection
        for i in range(self._deck_cb.count()):
            if self._deck_cb.itemData(i) == prev_id:
                self._deck_cb.setCurrentIndex(i)
                break
        self._deck_cb.blockSignals(False)
        if prev_id and self._current_container:
            await self._load_deck_cards(prev_id)

    def _on_deck_changed(self, _index: int):
        container_id = self._deck_cb.currentData()
        if container_id is None:
            self._cards = []
            self._filtered = []
            self._current_container = None
            self._populate_table([])
            self._update_stats()
            self._set_buttons_enabled(False)
            return
        asyncio.ensure_future(self._load_deck_cards(container_id))

    async def _load_deck_cards(self, container_id: int):
        from desktop.db import db
        try:
            # Get container metadata
            all_containers = await db.list_containers()
            self._current_container = next(
                (c for c in all_containers if c["id"] == container_id), None
            )
            # Load all cards (no pagination limit needed for a single deck)
            self._cards = await db.list_cards(container_id=container_id, limit=500, sort="chaos")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not load deck: {exc}")
            return
        self._apply_filter()
        self._update_stats()
        self._set_buttons_enabled(True)

    # ------------------------------------------------------------------ #
    # Table population & filtering                                          #
    # ------------------------------------------------------------------ #

    def _apply_filter(self):
        name_filt = self._name_filter.text().strip().lower()
        type_filt = self._type_filter.currentData()

        self._filtered = [
            c for c in self._cards
            if (not name_filt or name_filt in (c.get("name_en") or "").lower()
                             or name_filt in (c.get("printed_name") or "").lower())
            and (not type_filt or _type_group(c) == type_filt)
        ]
        self._populate_table(self._filtered)

    def _populate_table(self, cards: list[dict]):
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(cards))
        for row, card in enumerate(cards):
            cmc  = card.get("cmc") or 0
            name = card.get("printed_name") or card.get("name_en") or ""
            tl   = card.get("type_line") or ""
            cont = card.get("container_name") or "—"
            price = card.get("price_eur")

            cmc_item = QTableWidgetItem()
            cmc_item.setData(Qt.ItemDataRole.DisplayRole, int(cmc))
            self._table.setItem(row, 0, cmc_item)

            name_item = QTableWidgetItem(name)
            if card.get("is_commander"):
                name_item.setText(f"⚔ {name}")
                name_item.setForeground(Qt.GlobalColor.yellow)
            name_item.setData(Qt.ItemDataRole.UserRole, card)
            self._table.setItem(row, 1, name_item)

            self._table.setItem(row, 2, QTableWidgetItem(_type_group(card)))
            self._table.setItem(row, 3, QTableWidgetItem(cont))

            price_item = QTableWidgetItem()
            price_item.setData(Qt.ItemDataRole.DisplayRole, float(price) if price else 0.0)
            price_item.setText(f"€{price:.2f}" if price else "—")
            self._table.setItem(row, 4, price_item)

        self._table.setSortingEnabled(True)

    def _update_stats(self):
        if not self._cards:
            self._stats_lbl.setText("")
            self._curve_lbl.setText("")
            return

        from core.deckbuilder import curve_analysis, color_identity as _ci
        from core.analysis import detect_archetypes, deck_synergy_score, curve_fit_score
        import json

        commander = next((c for c in self._cards if c.get("is_commander")), None)
        total_value = sum(c.get("price_eur") or 0 for c in self._cards)
        nonland = [(c, 1) for c in self._cards if "Land" not in (c.get("type_line") or "")]
        curve = curve_analysis(nonland)

        # Color identity
        colors: set[str] = set()
        for c in self._cards:
            ci = c.get("color_identity") or []
            if isinstance(ci, str):
                try: ci = json.loads(ci)
                except: ci = []
            colors |= set(ci)
        color_str = "".join(sorted(colors, key=lambda x: "WUBRG".index(x) if x in "WUBRG" else 9))

        cmd_name = (commander.get("name_en") or "") if commander else ""
        fmt = (self._current_container or {}).get("deck_format") or (self._current_container or {}).get("type") or ""
        fmt_key = "commander" if fmt == "commander" else "60"

        # Archetype detection
        archetypes = detect_archetypes([c for c, _ in nonland])
        top_arch = archetypes[0][0] if archetypes else ""
        arch_conf = archetypes[0][1] if archetypes else 0.0

        # Synergy score (sample)
        synergy = deck_synergy_score([c for c, _ in nonland[:40]])

        # Curve fit vs detected archetype
        fit = curve_fit_score(curve, top_arch, fmt=fmt_key) if top_arch else 0.0

        parts = []
        if cmd_name:
            parts.append(f"⚔ {cmd_name}")
        if color_str:
            parts.append(f"[{color_str}]")
        if fmt:
            parts.append(fmt)
        parts.append(f"{len(self._cards)} cards")
        parts.append(f"€{total_value:.2f}")
        if top_arch:
            parts.append(f"{top_arch} {int(arch_conf * 100)}%")
        parts.append(f"Synergy {synergy:.1f}")
        parts.append(f"Curve fit {int(fit * 100)}%")
        self._stats_lbl.setText("  ·  ".join(parts))

        # Compact curve with archetype ideal overlay
        if curve:
            from core.analysis import ideal_curve
            ideal = ideal_curve(top_arch or "default", fmt=fmt_key)
            total_nonland = sum(curve.values())
            max_n = max(curve.values(), default=1)
            BAR = 8
            labels = {0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6+"}
            curve_parts = []
            for b in range(7):
                n = curve.get(b, 0)
                if n == 0:
                    continue
                bar = "█" * max(1, round(n / max_n * BAR))
                target = round(ideal.get(b, 0) * total_nonland)
                delta = n - target
                sign = f"+{delta}" if delta > 0 else str(delta)
                tag = f"({sign})" if delta != 0 else ""
                curve_parts.append(f"{labels[b]}:{bar}{n}{tag}")
            self._curve_lbl.setText("  ".join(curve_parts))
        else:
            self._curve_lbl.setText("")

    def _set_buttons_enabled(self, enabled: bool):
        self._add_btn.setEnabled(enabled)
        self._exp_full_btn.setEnabled(enabled)
        self._exp_mtga_btn.setEnabled(enabled)

    def _on_selection_changed(self):
        has_selection = bool(self._table.selectionModel().selectedRows())
        self._remove_btn.setEnabled(has_selection and self._current_container is not None)

    # ------------------------------------------------------------------ #
    # Context menu                                                          #
    # ------------------------------------------------------------------ #

    def _on_context_menu(self, pos):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        cards = [self._table.item(r.row(), 1).data(Qt.ItemDataRole.UserRole) for r in rows]

        menu = QMenu(self)
        remove_act = menu.addAction(f"Remove {len(cards)} card(s) from deck")
        menu.addSeparator()
        move_act = menu.addAction("Move to container…")

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == remove_act:
            asyncio.ensure_future(self._remove_cards(cards))
        elif action == move_act:
            asyncio.ensure_future(self._move_cards(cards))

    # ------------------------------------------------------------------ #
    # Edit operations                                                       #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_remove_selected(self):
        rows = self._table.selectionModel().selectedRows()
        cards = [self._table.item(r.row(), 1).data(Qt.ItemDataRole.UserRole) for r in rows]
        if cards:
            await self._remove_cards(cards)

    async def _remove_cards(self, cards: list[dict]):
        from desktop.db import db
        reply = QMessageBox.question(
            self, "Remove from deck",
            f"Remove {len(cards)} card(s) from this deck container?\n"
            "Cards will have no container assigned afterwards.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        card_ids = [c["id"] for c in cards if c.get("id")]
        try:
            await db.move_cards_to_container(card_ids, None)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load_deck_cards(self._current_container["id"])

    async def _move_cards(self, cards: list[dict]):
        from desktop.db import db
        containers = await db.list_containers()
        current_id = self._current_container["id"] if self._current_container else None
        choices = [c for c in containers if c["id"] != current_id]

        dlg = _ContainerPickerDialog(choices, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target_id = dlg.selected_id()
        if target_id is None:
            return
        card_ids = [c["id"] for c in cards if c.get("id")]
        try:
            await db.move_cards_to_container(card_ids, target_id)
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load_deck_cards(self._current_container["id"])

    @asyncSlot()
    async def _on_add_cards(self):
        """Open picker showing cards not in any deck/commander container."""
        from desktop.db import db
        try:
            pool = await db.get_all(exclude_container_types=["deck", "commander"])
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return

        dlg = _AddCardsDialog(pool, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        card_ids = dlg.selected_ids()
        if not card_ids or self._current_container is None:
            return
        try:
            await db.move_cards_to_container(card_ids, self._current_container["id"])
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return
        await self._load_deck_cards(self._current_container["id"])

    # ------------------------------------------------------------------ #
    # Export                                                                #
    # ------------------------------------------------------------------ #

    def _on_export(self, mtga: bool):
        from core.deckbuilder import format_container_decklist
        from datetime import date

        if not self._cards or self._current_container is None:
            return
        deck_name = self._current_container.get("name", "deck")
        text = format_container_decklist(self._cards, deck_name=deck_name, mtga=mtga)
        suffix = "_mtga" if mtga else "_full"
        default_name = f"{deck_name.replace(' ', '_')}{suffix}_{date.today()}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Decklist", default_name, "Text files (*.txt)"
        )
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))


# ── Dialogs ───────────────────────────────────────────────────────────────────

class _ContainerPickerDialog(QDialog):
    def __init__(self, containers: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move to container")
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select target container:"))
        self._list = QListWidget()
        for c in containers:
            item = QListWidgetItem(f"{c['name']}  [{c.get('type', '')}]")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self._list.addItem(item)
        layout.addWidget(self._list)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._list.itemDoubleClicked.connect(lambda _: self.accept())

    def selected_id(self):
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class _AddCardsDialog(QDialog):
    def __init__(self, pool: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add cards from collection")
        self.setMinimumSize(500, 400)
        layout = QVBoxLayout(self)

        filter_row = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by name…")
        self._filter.setClearButtonEnabled(True)
        filter_row.addWidget(self._filter)
        layout.addLayout(filter_row)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._list)

        layout.addWidget(QLabel("Hold Ctrl/Shift to select multiple cards."))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._pool = pool
        self._filter.textChanged.connect(self._apply_filter)
        self._apply_filter("")

    def _apply_filter(self, text: str):
        filt = text.strip().lower()
        self._list.clear()
        for card in self._pool:
            name = card.get("name_en") or ""
            if filt and filt not in name.lower():
                continue
            cont = card.get("container_name") or "no container"
            cmc = card.get("cmc") or 0
            item = QListWidgetItem(f"{name}  (CMC {int(cmc)})  📦 {cont}")
            item.setData(Qt.ItemDataRole.UserRole, card["id"])
            self._list.addItem(item)

    def selected_ids(self) -> list[int]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self._list.selectedItems()]
