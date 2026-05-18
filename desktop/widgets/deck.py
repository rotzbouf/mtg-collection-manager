"""Deck builder tab widget."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from qasync import asyncSlot


FORMATS = [
    ("commander", "Commander (100-card EDH)"),
    ("timeless",  "60-card — Timeless"),
    ("standard",  "60-card — Standard"),
]


class DeckWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: dict | None = None
        self._last_fmt: str = ""
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(QLabel("<h2>Deck Builder</h2>"))

        # Format selector
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._fmt_cb = QComboBox()
        for val, label in FORMATS:
            self._fmt_cb.addItem(label, val)
        fmt_row.addWidget(self._fmt_cb)
        fmt_row.addStretch()
        root.addLayout(fmt_row)

        # Commander name (only relevant for Commander format)
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("Commander:"))
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText("Leave blank to auto-pick best commander…")
        cmd_row.addWidget(self._cmd_edit)
        root.addLayout(cmd_row)

        self._cmd_hint = QLabel(
            "<small>For Commander format only. Enter a card name or leave blank.</small>"
        )
        root.addWidget(self._cmd_hint)

        # Build button
        build_row = QHBoxLayout()
        self._build_btn = QPushButton("Build deck")
        self._build_btn.setFixedWidth(160)
        build_row.addWidget(self._build_btn)
        build_row.addStretch()
        root.addLayout(build_row)

        # Status / stats
        self._stats_label = QLabel("")
        root.addWidget(self._stats_label)

        # Output
        root.addWidget(QLabel("<b>Decklist:</b>"))
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(_monofont())
        root.addWidget(self._output)

        # Action buttons row
        action_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy to clipboard")
        self._copy_btn.setEnabled(False)
        action_row.addWidget(self._copy_btn)

        self._export_full_btn = QPushButton("Export .txt")
        self._export_full_btn.setToolTip("Save decklist with container locations (picking reference)")
        self._export_full_btn.setEnabled(False)
        action_row.addWidget(self._export_full_btn)

        self._export_mtga_btn = QPushButton("Export MTGA/Moxfield")
        self._export_mtga_btn.setToolTip("Save clean format importable into MTGA, Moxfield, etc.")
        self._export_mtga_btn.setEnabled(False)
        action_row.addWidget(self._export_mtga_btn)

        action_row.addStretch()
        root.addLayout(action_row)

        # Signals
        self._fmt_cb.currentIndexChanged.connect(self._on_format_changed)
        self._build_btn.clicked.connect(self._on_build)
        self._copy_btn.clicked.connect(self._on_copy)
        self._export_full_btn.clicked.connect(lambda: self._on_export(mtga=False))
        self._export_mtga_btn.clicked.connect(lambda: self._on_export(mtga=True))
        self._on_format_changed()

    def _on_format_changed(self):
        is_commander = self._fmt_cb.currentData() == "commander"
        self._cmd_edit.setEnabled(is_commander)
        self._cmd_hint.setVisible(is_commander)

    # ------------------------------------------------------------------ #
    # Build                                                                 #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_build(self):
        from desktop.db import db, scryfall
        from core.deckbuilder import (
            build_commander_deck, build_60_deck,
            format_commander_decklist, format_60_decklist,
            rank_commanders,
        )

        fmt = self._fmt_cb.currentData()
        self._build_btn.setEnabled(False)
        self._stats_label.setText("Loading collection…")
        self._output.setPlainText("")
        self._copy_btn.setEnabled(False)
        self._export_full_btn.setEnabled(False)
        self._export_mtga_btn.setEnabled(False)

        try:
            pool = await db.get_all()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load collection:\n{exc}")
            self._build_btn.setEnabled(True)
            self._stats_label.setText("")
            return

        if fmt == "commander":
            cmd_name = self._cmd_edit.text().strip()
            if cmd_name:
                # Look up the commander in the pool first, then Scryfall
                commander = next(
                    (
                        c for c in pool
                        if (c.get("name_en") or "").lower() == cmd_name.lower()
                    ),
                    None,
                )
                if commander is None:
                    self._stats_label.setText("Commander not found in collection — searching Scryfall…")
                    try:
                        commander, _lang = await scryfall.resolve_card(cmd_name)
                    except Exception as exc:
                        QMessageBox.critical(self, "Error", str(exc))
                        self._build_btn.setEnabled(True)
                        self._stats_label.setText("")
                        return
                    if commander is None:
                        QMessageBox.warning(self, "Not found", f"'{cmd_name}' not found on Scryfall.")
                        self._build_btn.setEnabled(True)
                        self._stats_label.setText("")
                        return
            else:
                # Auto-pick best commander
                ranked = rank_commanders(pool)
                if not ranked:
                    QMessageBox.warning(
                        self, "No commanders",
                        "No commander-eligible legendary creatures found in the collection."
                    )
                    self._build_btn.setEnabled(True)
                    self._stats_label.setText("")
                    return
                commander, score = ranked[0]
                self._stats_label.setText(
                    f"Auto-selected: {commander.get('name_en', '')} (synergy score: {score})"
                )

            result = build_commander_deck(commander, pool)
            text = format_commander_decklist(result)
            total = result["collection_count"] + len(result.get("basics", {}))
            val = result.get("value_eur", 0)
            self._stats_label.setText(
                f"Commander: {commander.get('name_en', '')}  |  "
                f"{result['collection_count']} from collection  |  "
                f"€{val:.2f}"
            )
        else:
            result = build_60_deck(pool, fmt)
            text = format_60_decklist(result)
            val = result.get("value_eur", 0)
            strategy = result.get("strategy", "")
            self._stats_label.setText(
                f"Strategy: {strategy}  |  "
                f"{result['collection_count']} from collection  |  "
                f"€{val:.2f}"
            )

        self._result = result
        self._last_fmt = fmt
        self._output.setPlainText(text)
        self._copy_btn.setEnabled(True)
        self._export_full_btn.setEnabled(True)
        self._export_mtga_btn.setEnabled(True)
        self._build_btn.setEnabled(True)

    def _on_copy(self):
        text = self._output.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    def _on_export(self, mtga: bool):
        from datetime import date
        from core.deckbuilder import (
            format_commander_decklist, format_commander_decklist_mtga,
            format_60_decklist, format_60_decklist_mtga,
        )

        result = self._result
        fmt = self._last_fmt
        if not result:
            return

        if fmt == "commander":
            cmd_name = (result["commander"].get("name_en") or "deck").replace(" ", "_")
            suffix = "_mtga" if mtga else "_full"
            default_name = f"{cmd_name}{suffix}_{date.today()}.txt"
            text = format_commander_decklist_mtga(result) if mtga else format_commander_decklist(result)
        else:
            suffix = "_mtga" if mtga else "_full"
            default_name = f"{fmt}{suffix}_{date.today()}.txt"
            text = format_60_decklist_mtga(result) if mtga else format_60_decklist(result)

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Decklist", default_name,
            "Text files (*.txt);;All files (*)",
        )
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))


def _monofont():
    from PyQt6.QtGui import QFont
    font = QFont("Monospace")
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPointSize(9)
    return font
