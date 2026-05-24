"""Deck Analysis tab — inspect and edit decks stored as containers."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

_log = logging.getLogger(__name__)


def _bg(coro):
    """Schedule a coroutine as a fire-and-forget task with error logging."""
    task = asyncio.ensure_future(coro)
    task.add_done_callback(
        lambda f: _log.error("Background task failed: %s", f.exception())
        if not f.cancelled() and f.exception() else None
    )
    return task

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QMenu, QMessageBox, QFileDialog, QDialog,
    QDialogButtonBox, QListWidget, QListWidgetItem, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QAction, QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from qasync import asyncSlot

_MANA_DIR = Path(__file__).parent.parent.parent / "images" / "mana"


def _parse_mana_cost(mana_cost: str) -> list[str]:
    """Return individual symbols from a Scryfall mana cost string like '{2}{W}{U}'."""
    return re.findall(r'\{([^}]+)\}', mana_cost or "")


def _mana_pixmap(symbol: str, size: int = 22) -> QPixmap | None:
    """Render one mana symbol SVG to a QPixmap. Returns None when no SVG exists."""
    normalized = symbol.replace("/", "")  # W/U → WU for hybrid symbols
    path = _MANA_DIR / f"{normalized}.svg"
    if not path.exists():
        return None
    renderer = QSvgRenderer(str(path))
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    renderer.render(p)
    p.end()
    return px


_ARCHETYPE_DESCRIPTIONS: dict[str, str] = {
    "Aggro":       "Fast damage through low-cost creatures and combat tricks.",
    "Control":     "Answer threats with counters and board wipes, win on card advantage.",
    "Midrange":    "Efficient creatures and value plays that adapt to the game state.",
    "Ramp":        "Accelerate mana to cast powerful threats ahead of curve.",
    "Tokens":      "Flood the board with creature tokens and overwhelm through numbers.",
    "Graveyard":   "Exploit the graveyard as a resource through recursion and reanimation.",
    "Combo":       "Assemble specific card combinations for game-ending effects.",
    "Voltron":     "Buff a single powerful creature with equipment and auras.",
    "Spellslinger":"Generate value and damage by casting many instants and sorceries.",
}

_ARCH_COLORS: dict[str, str] = {
    "Aggro":       "#e74c3c",
    "Control":     "#3498db",
    "Midrange":    "#27ae60",
    "Ramp":        "#1abc9c",
    "Tokens":      "#f39c12",
    "Graveyard":   "#8e44ad",
    "Combo":       "#e67e22",
    "Voltron":     "#c0a020",
    "Spellslinger":"#00bcd4",
}

# Gradient bar colors per CMC bucket (0-6+)
_CMC_COLORS = ["#64b5f6", "#81c784", "#aed581", "#ffb74d", "#ff8a65", "#ef5350", "#b71c1c"]


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
        self._containers: list[dict] = []
        self._cards: list[dict] = []
        self._filtered: list[dict] = []
        self._current_container: dict | None = None
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_decks)

    def refresh(self):
        _bg(self._async_load_decks())

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

        # Commander header (visible only for commander decks)
        self._cmd_header = QWidget()
        self._cmd_header.setVisible(False)
        cmd_row = QHBoxLayout(self._cmd_header)
        cmd_row.setContentsMargins(0, 4, 0, 2)
        cmd_row.setSpacing(8)
        self._cmd_name_lbl = QLabel()
        self._cmd_name_lbl.setStyleSheet(
            "font-size: 17px; font-weight: bold; color: #f8d000;"
        )
        cmd_row.addWidget(self._cmd_name_lbl)
        self._cmd_icons_widget = QWidget()
        icons_lay = QHBoxLayout(self._cmd_icons_widget)
        icons_lay.setContentsMargins(0, 0, 0, 0)
        icons_lay.setSpacing(3)
        cmd_row.addWidget(self._cmd_icons_widget)
        cmd_row.addStretch()
        root.addWidget(self._cmd_header)

        # Stats bar (format, counts, value)
        self._stats_lbl = QLabel("")
        self._stats_lbl.setWordWrap(True)
        self._stats_lbl.setStyleSheet("color: #aaa; font-size: 11px; padding: 2px 0;")
        root.addWidget(self._stats_lbl)

        # Deck rating badge
        self._rating_lbl = QLabel("")
        self._rating_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._rating_lbl.setStyleSheet("font-size: 12px; padding: 2px 0;")
        root.addWidget(self._rating_lbl)

        # Strategy / archetype section
        self._strategy_lbl = QLabel("")
        self._strategy_lbl.setWordWrap(True)
        self._strategy_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._strategy_lbl.setStyleSheet("font-size: 11px; padding: 3px 0;")
        root.addWidget(self._strategy_lbl)

        # Mana curve chart
        self._curve_fig = Figure(figsize=(7, 2.4), tight_layout=True)
        self._curve_canvas = FigureCanvas(self._curve_fig)
        self._curve_canvas.setMinimumHeight(200)
        self._curve_canvas.setMaximumHeight(270)
        root.addWidget(self._curve_canvas)

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
        self._table.setHorizontalHeaderLabels(["MV", "Name", "Type", "Container", "€"])
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
        self._manifest_btn = QPushButton("📋 Manifest…")
        self._manifest_btn.setToolTip("View, print, or export the card picking manifest sorted by container")
        self._manifest_btn.setEnabled(False)
        bar.addWidget(self._manifest_btn)
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
        self._manifest_btn.clicked.connect(self._on_manifest)
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
        _bg(self._load_deck_cards(container_id))

    async def _load_deck_cards(self, container_id: int):
        from desktop.db import db
        try:
            all_containers = await db.list_containers()
            self._current_container = next(
                (c for c in all_containers if c["id"] == container_id), None
            )
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
            self._cmd_header.setVisible(False)
            self._stats_lbl.setText("")
            self._rating_lbl.setText("")
            self._strategy_lbl.setText("")
            self._curve_fig.clear()
            self._curve_canvas.draw()
            return

        from core.deckbuilder import curve_analysis
        from core.analysis import detect_archetypes, deck_synergy_score, curve_fit_score, ideal_curve
        import json

        commander = next((c for c in self._cards if c.get("is_commander")), None)
        total_value = sum(c.get("price_eur") or 0 for c in self._cards)
        nonland = [(c, 1) for c in self._cards if "Land" not in (c.get("type_line") or "")]
        land_count = len(self._cards) - len(nonland)
        curve = curve_analysis(nonland)

        colors: set[str] = set()
        for c in self._cards:
            ci = c.get("color_identity") or []
            if isinstance(ci, str):
                try:
                    ci = json.loads(ci)
                except Exception:
                    ci = []
            colors |= set(ci)
        color_str = "".join(sorted(colors, key=lambda x: "WUBRG".index(x) if x in "WUBRG" else 9))

        cmd_name = (commander.get("name_en") or "") if commander else ""
        fmt = (self._current_container or {}).get("deck_format") or (self._current_container or {}).get("type") or ""
        fmt_key = "commander" if fmt == "commander" else "60"

        archetypes = detect_archetypes([c for c, _ in nonland])
        top_arch = archetypes[0][0] if archetypes else ""
        synergy = deck_synergy_score([c for c, _ in nonland[:40]])
        fit = curve_fit_score(curve, top_arch, fmt=fmt_key) if top_arch else 0.0

        # Commander header
        if commander:
            self._cmd_name_lbl.setText(f"⚔  {cmd_name}")
            import json as _json
            _ci_raw = commander.get("color_identity") or []
            if isinstance(_ci_raw, str):
                try:
                    _ci_raw = _json.loads(_ci_raw)
                except Exception:
                    _ci_raw = []
            _ORDER = "WUBRG"
            _ci_syms = sorted(_ci_raw, key=lambda x: _ORDER.index(x) if x in _ORDER else 9)
            self._update_cmd_mana_icons(_ci_syms)
            self._cmd_header.setVisible(True)
        else:
            self._cmd_header.setVisible(False)

        # Stats line: (colors for non-commander) · format · card counts · value
        parts = []
        if not commander and color_str:
            parts.append(f"[{color_str}]")
        if fmt:
            parts.append(fmt)
        parts.append(f"{len(self._cards)} cards  ({len(nonland)} nonland · {land_count} lands)")
        parts.append(f"€{total_value:.2f}")
        self._stats_lbl.setText("  ·  ".join(parts))

        # Strategy section
        self._render_strategy(archetypes, synergy, fit)

        # Deck rating
        self._render_rating(self._cards, fmt_key, top_arch, synergy=synergy, curve_fit=fit,
                            archetype_conf=archetypes[0][1] if archetypes else 0.0)

        # Mana curve chart
        if curve:
            ideal = ideal_curve(top_arch or "default", fmt=fmt_key)
            self._render_curve_chart(curve, ideal, top_arch)
        else:
            self._curve_fig.clear()
            self._curve_canvas.draw()

    def _update_cmd_mana_icons(self, symbols: list[str]):
        layout = self._cmd_icons_widget.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for sym in symbols:
            px = _mana_pixmap(sym)
            if px is None:
                continue
            lbl = QLabel()
            lbl.setPixmap(px)
            lbl.setFixedSize(22, 22)
            layout.addWidget(lbl)

    def _render_strategy(self, archetypes: list, synergy: float, fit: float):
        if not archetypes:
            self._strategy_lbl.setText(
                '<span style="color:#666; font-style:italic;">No archetype detected.</span>'
            )
            return

        top_arch, top_conf = archetypes[0]
        desc = _ARCHETYPE_DESCRIPTIONS.get(top_arch, "")

        badges = []
        for arch, conf in archetypes[:4]:
            color = _ARCH_COLORS.get(arch, "#555")
            pct = int(conf * 100)
            badges.append(
                f'<span style="background:{color}; color:white; '
                f'padding:2px 7px; border-radius:3px; margin-right:5px; '
                f'font-weight:bold; font-size:11px;">'
                f'{arch}&nbsp;{pct}%</span>'
            )

        synergy_color = "#27ae60" if synergy >= 7 else "#f39c12" if synergy >= 4 else "#e74c3c"
        fit_color     = "#27ae60" if fit >= 0.7    else "#f39c12" if fit >= 0.4    else "#e74c3c"

        html = (
            f'{"".join(badges)}'
            f'<br/>'
            f'<span style="color:#aaa; font-style:italic; font-size:10px;">{desc}</span>'
            f'&nbsp;&nbsp;'
            f'<span style="color:#666; font-size:10px;">Synergy </span>'
            f'<span style="color:{synergy_color}; font-size:10px; font-weight:bold;">'
            f'{synergy:.1f}/10</span>'
            f'&nbsp;&nbsp;'
            f'<span style="color:#666; font-size:10px;">Curve fit </span>'
            f'<span style="color:{fit_color}; font-size:10px; font-weight:bold;">'
            f'{int(fit * 100)}%</span>'
        )
        self._strategy_lbl.setText(html)

    def _render_rating(self, cards: list, fmt: str, archetype: str,
                       *, synergy: float, curve_fit: float, archetype_conf: float):
        from core.analysis import rate_deck

        _GRADE_COLORS = {
            "S": "#f8d000", "A": "#27ae60", "B": "#2ecc71",
            "C": "#f39c12", "D": "#e67e22", "F": "#e74c3c",
        }
        _ROLE_LABELS = {
            "ramp": "Ramp", "removal": "Removal", "draw": "Draw",
            "board_wipe": "Wipes", "wincon": "Win cons",
        }

        rating = rate_deck(
            cards, fmt, archetype,
            synergy=synergy, curve_fit=curve_fit, archetype_conf=archetype_conf,
        )
        grade = rating["grade"]
        overall = rating["overall"]
        comp = rating["components"]
        roles = rating["role_detail"]

        grade_color = _GRADE_COLORS.get(grade, "#aaa")
        grade_html = (
            f'<span style="font-size:15px; font-weight:bold; color:{grade_color};">'
            f'{grade}</span>'
            f'<span style="color:#666; font-size:10px;"> &nbsp;{overall:.0f}/100</span>'
            f'&nbsp;&nbsp;'
            f'<span style="color:#555; font-size:10px;">'
            f'Synergy&nbsp;{comp["synergy"]:.0f}&nbsp;·&nbsp;'
            f'Curve&nbsp;{comp["curve"]:.0f}&nbsp;·&nbsp;'
            f'Roles&nbsp;{comp["roles"]:.0f}&nbsp;·&nbsp;'
            f'Coherence&nbsp;{comp["coherence"]:.0f}'
            f'</span>'
        )

        missing = [_ROLE_LABELS[r] for r, ok in roles.items() if not ok]
        if missing:
            warn = (
                f'&nbsp;&nbsp;<span style="color:#e67e22; font-size:10px;">'
                f'Missing: {", ".join(missing)}</span>'
            )
            grade_html += warn

        self._rating_lbl.setText(grade_html)

    def _render_curve_chart(self, curve: dict, ideal: dict, archetype: str):
        self._curve_fig.clear()
        self._curve_fig.patch.set_facecolor("#1e1e1e")

        ax = self._curve_fig.add_subplot(111)
        ax.set_facecolor("#252525")

        x = list(range(7))
        actual = [curve.get(i, 0) for i in range(7)]
        total_nonland = max(sum(actual), 1)
        ideal_counts = [ideal.get(i, 0) * total_nonland for i in range(7)]
        max_y = max(max(actual, default=0), max(ideal_counts, default=0))

        bars = ax.bar(x, actual, color=_CMC_COLORS, width=0.55, zorder=2)

        # Ideal curve overlay
        ax.plot(
            x, ideal_counts,
            color="#ffffff", linewidth=1.8, linestyle="--",
            marker="o", markersize=5, alpha=0.55, zorder=3,
            label=f"Ideal · {archetype}" if archetype else "Ideal",
        )

        # Count labels on top of each bar
        for bar, count in zip(bars, actual):
            if count > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max_y * 0.03,
                    str(count),
                    ha="center", va="bottom",
                    color="white", fontsize=9, fontweight="bold",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(["0", "1", "2", "3", "4", "5", "6+"], color="#ccc", fontsize=9)
        ax.set_ylabel("Cards", color="#888", fontsize=8, labelpad=4)
        ax.set_xlabel("Mana Value", color="#888", fontsize=8, labelpad=4)
        ax.set_xlim(-0.5, 6.5)
        ax.set_ylim(0, max_y * 1.3 + 1)
        ax.tick_params(axis="y", colors="#888", labelsize=8)
        ax.tick_params(axis="x", colors="#888")

        for spine in ax.spines.values():
            spine.set_edgecolor("#3a3a3a")
        ax.yaxis.grid(True, color="#333", linewidth=0.6, linestyle=":", zorder=0)
        ax.set_axisbelow(True)

        if archetype:
            legend = ax.legend(
                fontsize=8, facecolor="#2a2a2a", edgecolor="#444",
                labelcolor="white", loc="upper right", framealpha=0.8,
            )

        self._curve_fig.tight_layout(pad=0.6)
        self._curve_canvas.draw()

    def _set_buttons_enabled(self, enabled: bool):
        self._add_btn.setEnabled(enabled)
        self._manifest_btn.setEnabled(enabled)
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
            _bg(self._remove_cards(cards))
        elif action == move_act:
            _bg(self._move_cards(cards))

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

    def _on_manifest(self):
        from core.deckbuilder import format_container_location_manifest
        from desktop.widgets.deck import _ManifestDialog
        if not self._cards or self._current_container is None:
            return
        deck_name = self._current_container.get("name", "")
        text = format_container_location_manifest(self._cards, deck_name=deck_name)
        if not text:
            QMessageBox.information(self, "Manifest", "No cards with container data.")
            return
        dlg = _ManifestDialog(text, parent=self)
        dlg.exec()

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
            item = QListWidgetItem(f"{name}  (MV {int(cmc)})  📦 {cont}")
            item.setData(Qt.ItemDataRole.UserRole, card["id"])
            self._list.addItem(item)

    def selected_ids(self) -> list[int]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self._list.selectedItems()]
