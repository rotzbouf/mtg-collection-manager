"""Stats tab widget."""
from __future__ import annotations

from datetime import datetime

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLabel, QPushButton, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from qasync import asyncSlot

from desktop.utils import async_pixmap


# ─────────────────────────────────────────────────────────────────────────────
# Table helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bold_font():
    from PyQt6.QtGui import QFont
    f = QFont()
    f.setBold(True)
    return f


def _base_table(rows: int, cols: int) -> QTableWidget:
    t = QTableWidget(rows, cols)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
    t.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return t


def _fit_table(t: QTableWidget) -> QTableWidget:
    """Resize all columns to content, then fix the widget width/height to match."""
    t.resizeColumnsToContents()
    t.resizeRowsToContents()
    total_w = sum(t.columnWidth(c) for c in range(t.columnCount())) + 6
    total_h = t.horizontalHeader().height() + sum(
        t.rowHeight(r) for r in range(t.rowCount())
    ) + 6
    t.setFixedSize(total_w, total_h)
    return t


def _kv_table(rows: list[tuple[str, str]]) -> QTableWidget:
    t = _base_table(len(rows), 2)
    t.horizontalHeader().setVisible(False)
    for r, (k, v) in enumerate(rows):
        key_item = QTableWidgetItem(f"  {k}")
        key_item.setForeground(Qt.GlobalColor.gray)
        val_item = QTableWidgetItem(v)
        val_item.setFont(_bold_font())
        t.setItem(r, 0, key_item)
        t.setItem(r, 1, val_item)
    return _fit_table(t)


def _header_table(headers: list[str], rows: list[list[str]]) -> QTableWidget:
    t = _base_table(len(rows), len(headers))
    t.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            t.setItem(r, c, QTableWidgetItem(f"  {val}" if c == 0 else val))
    return _fit_table(t)


def _section_header(text: str) -> QLabel:
    lbl = QLabel(f"<b>{text}</b>")
    lbl.setStyleSheet("font-size: 13px; padding-top: 10px;")
    return lbl


# ─────────────────────────────────────────────────────────────────────────────
# Card embed
# ─────────────────────────────────────────────────────────────────────────────

_EMBED_IMG_W = 140
_EMBED_IMG_H = 196
_EMBED_W     = 168


class _CardEmbed(QFrame):
    """Small embed: card thumbnail + name + price."""

    def __init__(self, card: dict, parent=None):
        super().__init__(parent)
        self._card = card
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setFixedWidth(_EMBED_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        self._img_lbl = QLabel()
        self._img_lbl.setFixedSize(_EMBED_IMG_W, _EMBED_IMG_H)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet(
            "background:#1e1e2e; border-radius:6px; color:#555; font-size:10px;"
        )
        self._img_lbl.setText("⋯")
        lay.addWidget(self._img_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        card = self._card
        name_en = card.get("name_en") or ""
        loc = card.get("printed_name") or card.get("name_de") or name_en
        display = f"{loc}\n({name_en})" if loc and loc != name_en else name_en

        name_lbl = QLabel(display)
        name_lbl.setWordWrap(True)
        name_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        name_lbl.setStyleSheet("font-size:11px; font-weight:bold;")
        lay.addWidget(name_lbl)

        eur = card.get("price_eur")
        price_lbl = QLabel(f"€{float(eur):.2f}" if eur else "—")
        price_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        price_lbl.setStyleSheet("font-size:14px; color:#4CAF50; font-weight:bold;")
        lay.addWidget(price_lbl)

        if card.get("foil"):
            foil_lbl = QLabel("★ Foil")
            foil_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            foil_lbl.setStyleSheet("font-size:10px; color:#FFD700;")
            lay.addWidget(foil_lbl)

        lay.addStretch()

    def load_image(self):
        self._do_load_image()

    @asyncSlot()
    async def _do_load_image(self):
        card = self._card
        pixmap = await async_pixmap(card.get("scryfall_id"), card.get("image_url"))
        if pixmap:
            scaled = pixmap.scaled(
                _EMBED_IMG_W, _EMBED_IMG_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setText("")
        else:
            self._img_lbl.setText("No image")


# ─────────────────────────────────────────────────────────────────────────────
# Stats widget
# ─────────────────────────────────────────────────────────────────────────────

class StatsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._embeds: list[_CardEmbed] = []
        self._value_chart_fig = None
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_stats)

    def refresh(self):
        self._load_stats()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("<h2>Collection Statistics</h2>"))
        top_row.addStretch()
        self._refresh_btn = QPushButton("Refresh")
        top_row.addWidget(self._refresh_btn)
        root.addLayout(top_row)
        self._refresh_btn.clicked.connect(self._load_stats)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        self._inner_layout = QVBoxLayout(inner)
        self._inner_layout.setSpacing(6)
        self._inner_layout.setContentsMargins(4, 0, 8, 8)
        scroll.setWidget(inner)
        root.addWidget(scroll)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_stats(self):
        from desktop.db import db, scryfall
        stats = await db.stats()
        container_stats = await db.container_stats()
        value_history = await db.get_collection_value_history()
        sets = await db.get_sets_summary()

        # Build sets_meta map: try DB cache first; if any set is missing, refresh
        # from Scryfall in the background and persist for next time.
        sets_meta = await db.get_sets_meta_map()
        collection_codes = {s["set_code"].lower() for s in sets if s.get("set_code")}
        missing = collection_codes - sets_meta.keys()
        if missing:
            try:
                all_sets = await scryfall.fetch_all_sets()
                if all_sets:
                    await db.upsert_sets_meta([
                        {"set_code": s["code"], "card_count": s.get("card_count")}
                        for s in all_sets
                        if s.get("code") and s.get("card_count") is not None
                    ])
                    sets_meta = await db.get_sets_meta_map()
            except Exception:
                pass  # non-fatal — table will populate on next load

        self._render_stats(stats, container_stats, value_history, sets, sets_meta)

    # ------------------------------------------------------------------ #
    # Rendering                                                             #
    # ------------------------------------------------------------------ #

    def _render_stats(self, stats: dict, container_stats: list[dict], value_history: list[dict], sets: list[dict] | None = None, sets_meta: dict[str, int] | None = None):
        if self._value_chart_fig is not None:
            plt.close(self._value_chart_fig)
            self._value_chart_fig = None

        lay = self._inner_layout
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear_layout(item.layout())
        self._embeds = []

        # ── Row 1: Overview + Rarity side by side ─────────────────────── #
        lay.addWidget(_section_header("Overview"))
        row1 = QHBoxLayout()
        row1.setSpacing(24)
        row1.setAlignment(Qt.AlignmentFlag.AlignLeft)

        overview_rows = [
            ("Total cards",       str(stats.get("total_cards", 0))),
            ("Unique cards",      str(stats.get("unique_cards", 0))),
            ("Foil cards",        str(stats.get("foil_total", 0))),
            ("Total value (EUR)", f"€{stats.get('total_value_eur') or 0:.2f}"),
            ("Total value (USD)", f"${stats.get('total_value_usd') or 0:.2f}"),
            ("Foil value (EUR)",  f"€{stats.get('foil_eur') or 0:.2f}"),
        ]
        row1.addWidget(_kv_table(overview_rows))

        rarity_rows = []
        for key, label in [
            ("r_common",   "Common"),
            ("r_uncommon", "Uncommon"),
            ("r_rare",     "Rare"),
            ("r_mythic",   "Mythic"),
        ]:
            rarity_rows.append([
                label,
                str(stats.get(key, 0)),
                f"€{stats.get(key + '_eur') or 0:.2f}",
            ])
        rarity_tbl = _header_table(["Rarity", "Count", "Value (EUR)"], rarity_rows)
        row1_right = QVBoxLayout()
        row1_right.setSpacing(2)
        row1_right.addWidget(_section_header("Rarity"))
        row1_right.addWidget(rarity_tbl)
        row1.addLayout(row1_right)

        row1.addStretch()
        lay.addLayout(row1)

        # ── Row 2: Language breakdown ──────────────────────────────────── #
        lay.addWidget(_section_header("Language Breakdown"))
        lang_rows = []
        for code, label in [("en", "English 🇬🇧"), ("de", "German 🇩🇪")]:
            lang_rows.append([
                label,
                str(stats.get(f"{code}_total", 0)),
                str(stats.get(f"{code}_nonfoil", 0)),
                f"€{stats.get(f'{code}_nonfoil_eur') or 0:.2f}",
                str(stats.get(f"{code}_foil", 0)),
                f"€{stats.get(f'{code}_foil_eur') or 0:.2f}",
            ])
        lang_row = QHBoxLayout()
        lang_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        lang_row.addWidget(_header_table(
            ["Language", "Total", "Non-foil", "NF value", "Foil", "Foil value"],
            lang_rows,
        ))
        lang_row.addStretch()
        lay.addLayout(lang_row)

        # ── Row 3: Top 10 most valuable — 2 rows × 5 ─────────────────── #
        lay.addWidget(_section_header("Top 10 Most Valuable Cards"))
        top_cards = stats.get("top_cards", [])
        embeds_grid = QGridLayout()
        embeds_grid.setSpacing(10)
        for i, card in enumerate(top_cards):
            embed = _CardEmbed(card)
            embeds_grid.addWidget(embed, i // 5, i % 5)
            self._embeds.append(embed)
        lay.addLayout(embeds_grid)

        QTimer.singleShot(50, self._load_embed_images)

        # ── Row 4: Containers ─────────────────────────────────────────── #
        if container_stats:
            lay.addWidget(_section_header("Containers"))
            ct_rows = []
            for c in container_stats:
                eur     = c.get("total_value_eur") or 0.0
                max_eur = c.get("max_card_eur")
                fmt     = c.get("deck_format") or "—"
                ct_rows.append([
                    c.get("name") or "",
                    c.get("type") or "",
                    fmt,
                    str(c.get("card_count", 0)),
                    f"€{eur:.2f}",
                    f"€{max_eur:.2f}" if max_eur else "—",
                ])
            ct_row = QHBoxLayout()
            ct_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
            ct_row.addWidget(_header_table(
                ["Name", "Type", "Format", "Cards", "Value (EUR)", "Best card (EUR)"],
                ct_rows,
            ))
            ct_row.addStretch()
            lay.addLayout(ct_row)

        # ── Row 5: Set completion ─────────────────────────────────────── #
        if sets:
            top_sets = sorted(sets, key=lambda s: s.get("distinct_names") or 0, reverse=True)[:10]
            lay.addWidget(_section_header(f"Set Completion  ({len(sets)} sets in collection)"))
            set_rows = []
            _meta = sets_meta or {}
            for s in top_sets:
                eur = s.get("total_value_eur")
                distinct = s.get("distinct_names") or 0
                code_lc = (s.get("set_code") or "").lower()
                total_in_set = _meta.get(code_lc)
                if total_in_set:
                    pct = distinct / total_in_set * 100
                    completion = f"{distinct} / {total_in_set}  ({pct:.0f}%)"
                else:
                    completion = str(distinct)
                set_rows.append([
                    (s.get("set_code") or "").upper(),
                    s.get("set_name") or s.get("set_code") or "",
                    completion,
                    str(s.get("card_count") or 0),
                    f"€{eur:.2f}" if eur else "—",
                ])
            set_row = QHBoxLayout()
            set_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
            set_row.addWidget(_header_table(
                ["Code", "Set name", "Distinct (completion)", "Copies", "Value (EUR)"],
                set_rows,
            ))
            set_row.addStretch()
            lay.addLayout(set_row)

        # ── Collection value over time chart ──────────────────────────── #
        lay.addWidget(_section_header("Collection Value Over Time (EUR)"))
        if len(value_history) < 2:
            no_data = QLabel(
                "Not enough price history yet.\n"
                "Run a price sync daily to build up the chart."
            )
            no_data.setStyleSheet("color: #666; font-size: 12px; padding: 8px 0;")
            lay.addWidget(no_data)
        else:
            dates = [datetime.fromisoformat(r["recorded_at"]) for r in value_history]
            values = [r["total_value_eur"] for r in value_history]

            fig, ax = plt.subplots(figsize=(8, 2.8))
            self._value_chart_fig = fig
            fig.patch.set_facecolor("#1e1e2e")
            ax.set_facecolor("#2a2a3e")

            ax.plot(dates, values, color="#4caf50", linewidth=2, marker="o", markersize=4)
            ax.fill_between(dates, values, alpha=0.15, color="#4caf50")

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            fig.autofmt_xdate(rotation=30, ha="right")

            ax.set_ylabel("EUR", color="#cccccc", fontsize=10)
            ax.tick_params(colors="#aaaaaa", labelsize=9)
            for spine in ax.spines.values():
                spine.set_edgecolor("#444466")

            delta = values[-1] - values[0]
            sign = "+" if delta >= 0 else ""
            ax.set_title(
                f"€{values[-1]:.2f}  ({sign}{delta:.2f} over {len(dates)} days)",
                color="#cccccc", fontsize=11, pad=6,
            )

            fig.tight_layout()
            canvas = FigureCanvas(fig)
            canvas.setMinimumHeight(220)
            canvas.setMaximumHeight(300)
            lay.addWidget(canvas)

        lay.addStretch()

    def _load_embed_images(self):
        for embed in self._embeds:
            embed.load_image()


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear_layout(item.layout())
