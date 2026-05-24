"""Format Bans tab — view and override per-format ban/restricted lists."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from qasync import asyncSlot

FORMATS = [
    ("standard",  "Standard"),
    ("modern",    "Modern"),
    ("legacy",    "Legacy"),
    ("vintage",   "Vintage"),
    ("pauper",    "Pauper"),
    ("commander", "Commander"),
]

_STATUS_COLORS = {
    "banned":     "#f38ba8",
    "restricted": "#fab387",
}


class FormatBansWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_ready = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(QLabel("<h2>Format Ban Lists</h2>"))

        # ── Toolbar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()

        bar.addWidget(QLabel("Format:"))
        self._fmt_cb = QComboBox()
        for key, label in FORMATS:
            self._fmt_cb.addItem(label, key)
        self._fmt_cb.setFixedWidth(160)
        bar.addWidget(self._fmt_cb)

        bar.addSpacing(16)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        bar.addWidget(self._status_lbl)

        bar.addStretch()

        self._add_override_btn = QPushButton("Add override")
        self._add_override_btn.setToolTip("Manually ban, restrict, or un-ban a card for this format")
        bar.addWidget(self._add_override_btn)

        self._remove_override_btn = QPushButton("Remove override")
        self._remove_override_btn.setEnabled(False)
        bar.addWidget(self._remove_override_btn)

        root.addLayout(bar)

        # ── Ban table ──────────────────────────────────────────────────────
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Card name", "Status", "Reason / override"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 100)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        root.addWidget(self._table)

        # ── Signals ────────────────────────────────────────────────────────
        self._fmt_cb.currentIndexChanged.connect(self._on_format_changed)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._add_override_btn.clicked.connect(self._on_add_override)
        self._remove_override_btn.clicked.connect(self._on_remove_override)

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def db_ready(self):
        self._db_ready = True
        QTimer.singleShot(0, self._load)

    def refresh(self):
        if self._db_ready:
            QTimer.singleShot(0, self._load)

    # ── Data loading ───────────────────────────────────────────────────────────

    def _on_format_changed(self, _index=None):
        if self._db_ready:
            QTimer.singleShot(0, self._load)

    @asyncSlot()
    async def _load(self):
        from desktop.db import db

        fmt = self._fmt_cb.currentData()
        if not fmt:
            return

        bans = await db.get_format_bans(fmt)
        overrides = await db.get_ban_overrides(fmt)
        override_names = {r["card_name"] for r in overrides}

        self._table.setRowCount(0)
        self._table.setRowCount(len(bans))

        for row_idx, entry in enumerate(bans):
            name      = entry.get("card_name", "")
            status    = entry.get("status", "")
            reason    = entry.get("reason") or ""
            is_over   = bool(entry.get("is_override"))

            name_item   = QTableWidgetItem(name)
            status_item = QTableWidgetItem(status.capitalize())
            reason_item = QTableWidgetItem(f"[override] {reason}" if is_over else reason)

            color = QColor(_STATUS_COLORS.get(status, "#cdd6f4"))
            status_item.setForeground(color)

            if is_over:
                reason_item.setForeground(QColor("#cba6f7"))
                name_item.setForeground(QColor("#cba6f7"))

            for col, item in enumerate((name_item, status_item, reason_item)):
                item.setData(Qt.ItemDataRole.UserRole, name)
                item.setData(Qt.ItemDataRole.UserRole + 1, is_over)
                self._table.setItem(row_idx, col, item)

        banned_count     = sum(1 for e in bans if e.get("status") == "banned")
        restricted_count = sum(1 for e in bans if e.get("status") == "restricted")
        override_count   = sum(1 for e in bans if e.get("is_override"))
        parts = []
        if banned_count:
            parts.append(f"{banned_count} banned")
        if restricted_count:
            parts.append(f"{restricted_count} restricted")
        if override_count:
            parts.append(f"{override_count} overridden")
        self._status_lbl.setText("  ·  ".join(parts) if parts else "No bans in collection")

        self._remove_override_btn.setEnabled(False)

    def _on_selection_changed(self):
        rows = self._table.selectedItems()
        if not rows:
            self._remove_override_btn.setEnabled(False)
            return
        row = self._table.currentRow()
        name_item = self._table.item(row, 0)
        is_override = name_item and bool(name_item.data(Qt.ItemDataRole.UserRole + 1))
        self._remove_override_btn.setEnabled(is_override)

    # ── Override dialogs ───────────────────────────────────────────────────────

    @asyncSlot()
    async def _on_add_override(self):
        fmt = self._fmt_cb.currentData()
        if not fmt:
            return
        dlg = _AddOverrideDialog(fmt, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        card_name, status, reason = dlg.values()
        from desktop.db import db
        await db.add_ban_override(fmt, card_name, status, reason or None)
        await self._load()

    @asyncSlot()
    async def _on_remove_override(self):
        row = self._table.currentRow()
        name_item = self._table.item(row, 0)
        if not name_item:
            return
        card_name = name_item.data(Qt.ItemDataRole.UserRole)
        fmt = self._fmt_cb.currentData()
        reply = QMessageBox.question(
            self, "Remove override",
            f"Remove override for '{card_name}' in {fmt}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from desktop.db import db
        await db.remove_ban_override(fmt, card_name)
        await self._load()


class _AddOverrideDialog(QDialog):
    def __init__(self, fmt: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Add ban override — {fmt.capitalize()}")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Exact English card name")
        form.addRow("Card name:", self._name_edit)

        self._status_cb = QComboBox()
        self._status_cb.addItem("Banned", "banned")
        self._status_cb.addItem("Restricted (Vintage — max 1 copy)", "restricted")
        self._status_cb.addItem("Legal (un-ban override)", "legal")
        form.addRow("Status:", self._status_cb)

        self._reason_edit = QLineEdit()
        self._reason_edit.setPlaceholderText("Optional reason / announcement URL")
        form.addRow("Reason:", self._reason_edit)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate(self):
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Missing name", "Please enter a card name.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return (
            self._name_edit.text().strip(),
            self._status_cb.currentData(),
            self._reason_edit.text().strip(),
        )
