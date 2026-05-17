"""Stats tab widget."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QFrame, QTableWidget,
    QTableWidgetItem, QHeaderView, QSizePolicy, QGroupBox,
    QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer
from qasync import asyncSlot

from desktop.utils import display_name


class StatsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        QTimer.singleShot(0, self._load_stats)

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # Refresh button
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("<h2>Collection Statistics</h2>"))
        top_row.addStretch()
        self._refresh_btn = QPushButton("Refresh")
        top_row.addWidget(self._refresh_btn)
        root.addLayout(top_row)
        self._refresh_btn.clicked.connect(self._load_stats)

        # Scrollable area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        self._inner_layout = QVBoxLayout(inner)
        self._inner_layout.setSpacing(16)
        scroll.setWidget(inner)
        root.addWidget(scroll)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_stats(self):
        from desktop.db import db

        stats = await db.stats()
        container_stats = await db.container_stats()
        self._render_stats(stats, container_stats)

    # ------------------------------------------------------------------ #
    # Rendering                                                             #
    # ------------------------------------------------------------------ #

    def _render_stats(self, stats: dict, container_stats: list[dict]):
        # Clear previous content
        while self._inner_layout.count():
            item = self._inner_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        layout = self._inner_layout

        # ---- Overview ----
        layout.addWidget(self._section_header("Overview"))
        overview = QGridLayout()
        self._add_kv(overview, 0, "Total cards", str(stats.get("total_cards", 0)))
        self._add_kv(overview, 1, "Unique cards", str(stats.get("unique_cards", 0)))
        self._add_kv(overview, 2, "Total value (EUR)", f"€{stats.get('total_value_eur', 0):.2f}")
        self._add_kv(overview, 3, "Total value (USD)", f"${stats.get('total_value_usd', 0):.2f}")
        self._add_kv(overview, 4, "Foil cards", str(stats.get("foil_total", 0)))
        self._add_kv(overview, 5, "Foil value (EUR)", f"€{stats.get('foil_eur', 0):.2f}")
        frame = QFrame()
        frame.setLayout(overview)
        layout.addWidget(frame)

        # ---- Language breakdown ----
        layout.addWidget(self._section_header("Language Breakdown"))
        lang_grid = QGridLayout()
        for col_idx, (key, label) in enumerate([
            ("en", "English"),
            ("de", "German"),
        ]):
            lang_grid.addWidget(QLabel(f"<b>{label}</b>"), 0, col_idx * 3)
            total = stats.get(f"{key}_total", 0)
            nf = stats.get(f"{key}_nonfoil", 0)
            f_ = stats.get(f"{key}_foil", 0)
            nf_eur = stats.get(f"{key}_nonfoil_eur", 0)
            f_eur = stats.get(f"{key}_foil_eur", 0)
            lang_grid.addWidget(QLabel(f"Total: {total}"), 1, col_idx * 3)
            lang_grid.addWidget(QLabel(f"Non-foil: {nf}  (€{nf_eur:.2f})"), 2, col_idx * 3)
            lang_grid.addWidget(QLabel(f"Foil: {f_}  (€{f_eur:.2f})"), 3, col_idx * 3)
        lang_frame = QFrame()
        lang_frame.setLayout(lang_grid)
        layout.addWidget(lang_frame)

        # ---- Rarity breakdown ----
        layout.addWidget(self._section_header("Rarity Breakdown"))
        rarity_grid = QGridLayout()
        headers = ["Rarity", "Count", "Value (EUR)"]
        for col, h in enumerate(headers):
            lbl = QLabel(f"<b>{h}</b>")
            rarity_grid.addWidget(lbl, 0, col)
        for row_idx, (key, label) in enumerate([
            ("r_common", "Common"),
            ("r_uncommon", "Uncommon"),
            ("r_rare", "Rare"),
            ("r_mythic", "Mythic"),
        ], start=1):
            rarity_grid.addWidget(QLabel(label), row_idx, 0)
            rarity_grid.addWidget(QLabel(str(stats.get(key, 0))), row_idx, 1)
            eur_key = key + "_eur"
            rarity_grid.addWidget(QLabel(f"€{stats.get(eur_key, 0):.2f}"), row_idx, 2)
        rarity_frame = QFrame()
        rarity_frame.setLayout(rarity_grid)
        layout.addWidget(rarity_frame)

        # ---- Top 5 most valuable ----
        layout.addWidget(self._section_header("Top 5 Most Valuable Cards"))
        top_cards = stats.get("top_cards", [])
        top_table = QTableWidget(len(top_cards), 4)
        top_table.setHorizontalHeaderLabels(["Name", "Foil", "Language", "Price (EUR)"])
        top_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        top_table.verticalHeader().setVisible(False)
        top_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row_idx, card in enumerate(top_cards):
            name = card.get("printed_name") or card.get("name_de") or card.get("name_en") or ""
            name_en = card.get("name_en") or ""
            display = f"{name} ({name_en})" if name and name != name_en else name_en
            top_table.setItem(row_idx, 0, QTableWidgetItem(display))
            top_table.setItem(row_idx, 1, QTableWidgetItem("★" if card.get("foil") else ""))
            top_table.setItem(row_idx, 2, QTableWidgetItem((card.get("language") or "").upper()))
            eur = card.get("price_eur")
            top_table.setItem(row_idx, 3, QTableWidgetItem(f"€{eur:.2f}" if eur else "—"))
        top_table.setMaximumHeight(180)
        layout.addWidget(top_table)

        # ---- Containers ----
        if container_stats:
            layout.addWidget(self._section_header("Containers"))
            ct_table = QTableWidget(len(container_stats), 4)
            ct_table.setHorizontalHeaderLabels(["Name", "Type", "Cards", "Value (EUR)"])
            ct_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            ct_table.verticalHeader().setVisible(False)
            ct_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            for row_idx, c in enumerate(container_stats):
                ct_table.setItem(row_idx, 0, QTableWidgetItem(c.get("name") or ""))
                ct_table.setItem(row_idx, 1, QTableWidgetItem(c.get("type") or ""))
                ct_table.setItem(row_idx, 2, QTableWidgetItem(str(c.get("card_count", 0))))
                eur = c.get("total_value_eur") or 0.0
                ct_table.setItem(row_idx, 3, QTableWidgetItem(f"€{eur:.2f}"))
            ct_table.setMaximumHeight(min(50 + len(container_stats) * 26, 300))
            layout.addWidget(ct_table)

        layout.addStretch()

    @staticmethod
    def _section_header(text: str) -> QLabel:
        lbl = QLabel(f"<h3>{text}</h3>")
        lbl.setContentsMargins(0, 8, 0, 0)
        return lbl

    @staticmethod
    def _add_kv(grid: QGridLayout, row: int, key: str, value: str):
        grid.addWidget(QLabel(f"{key}:"), row, 0)
        grid.addWidget(QLabel(f"<b>{value}</b>"), row, 1)
