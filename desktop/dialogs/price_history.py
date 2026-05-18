"""Price history dialog — shows EUR price over time as a line chart."""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
from PyQt6.QtCore import Qt
from qasync import asyncSlot

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from datetime import datetime


class PriceHistoryDialog(QDialog):
    def __init__(self, card: dict, parent=None):
        super().__init__(parent)
        self._card = card
        name = card.get("name_en") or card.get("printed_name") or "Card"
        self.setWindowTitle(f"Price history — {name}")
        self.resize(640, 380)
        self._build_ui()
        self._load()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._status_lbl = QLabel("Loading…")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_lbl)

        self._figure, self._ax = plt.subplots()
        self._figure.patch.set_facecolor("#1e1e2e")
        self._ax.set_facecolor("#2a2a3e")
        self._canvas = FigureCanvas(self._figure)
        self._canvas.setVisible(False)
        layout.addWidget(self._canvas)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _load(self):
        self._do_load()

    @asyncSlot()
    async def _do_load(self):
        from desktop.db import db

        sid = self._card.get("scryfall_id")
        if not sid:
            self._status_lbl.setText("No Scryfall ID — no history available.")
            return

        rows = await db.get_price_history(sid)
        if not rows:
            self._status_lbl.setText(
                "No price history recorded yet.\n"
                "Run a sync to start collecting price data."
            )
            return

        dates = [datetime.fromisoformat(r["recorded_at"]) for r in rows]
        prices = [r["price_eur"] for r in rows]

        ax = self._ax
        ax.clear()
        ax.set_facecolor("#2a2a3e")

        ax.plot(dates, prices, color="#e94560", linewidth=2, marker="o", markersize=4)
        ax.fill_between(dates, prices, alpha=0.15, color="#e94560")

        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self._figure.autofmt_xdate(rotation=30, ha="right")

        ax.set_ylabel("EUR", color="#cccccc", fontsize=11)
        ax.tick_params(colors="#aaaaaa", labelsize=9)
        for spine in ax.spines.values():
            spine.set_edgecolor("#444466")

        if len(prices) > 1:
            delta = prices[-1] - prices[0]
            sign = "+" if delta >= 0 else ""
            ax.set_title(
                f"€{prices[-1]:.2f}  ({sign}{delta:.2f} since first record)",
                color="#cccccc", fontsize=12, pad=8,
            )
        else:
            ax.set_title(f"€{prices[-1]:.2f}", color="#cccccc", fontsize=12, pad=8)

        self._figure.tight_layout()
        self._canvas.draw()

        self._status_lbl.setVisible(False)
        self._canvas.setVisible(True)
