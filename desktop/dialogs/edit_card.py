"""Edit card dialog."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLabel, QComboBox, QCheckBox, QPushButton,
)
from PyQt6.QtCore import pyqtSignal

from desktop.utils import CONDITIONS


class EditCardDialog(QDialog):
    """Edit the mutable fields of a collection entry."""

    saved = pyqtSignal(dict)  # emits {field: value, ...}

    def __init__(self, card: dict, containers: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit — {card.get('name_en', '')}")
        self.setMinimumWidth(380)
        self._card = card
        self._containers = containers or []
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        form = QFormLayout()

        # Card identity (read-only)
        form.addRow("Name:", QLabel(f"<b>{self._card.get('name_en', '')}</b>"))
        set_info = (
            f"{self._card.get('set_name', '')} "
            f"({(self._card.get('set_code') or '').upper()}) "
            f"#{self._card.get('collector_number', '')}"
        )
        form.addRow("Set:", QLabel(set_info))

        # Condition
        self._condition_cb = QComboBox()
        self._condition_cb.addItems(CONDITIONS)
        cur_cond = self._card.get("condition", "NM")
        if cur_cond in CONDITIONS:
            self._condition_cb.setCurrentIndex(CONDITIONS.index(cur_cond))
        form.addRow("Condition:", self._condition_cb)

        # Language
        self._lang_cb = QComboBox()
        lang_map = [
            ("en", "English"), ("de", "German"), ("fr", "French"),
            ("it", "Italian"), ("es", "Spanish"), ("pt", "Portuguese"),
            ("ja", "Japanese"), ("ko", "Korean"), ("ru", "Russian"),
            ("zhs", "Simplified Chinese"), ("zht", "Traditional Chinese"),
        ]
        for code, label in lang_map:
            self._lang_cb.addItem(label, code)
        cur_lang = (self._card.get("language") or "en").lower()
        for i in range(self._lang_cb.count()):
            if self._lang_cb.itemData(i) == cur_lang:
                self._lang_cb.setCurrentIndex(i)
                break
        form.addRow("Language:", self._lang_cb)

        # Foil
        self._foil_cb = QCheckBox("Foil")
        self._foil_cb.setChecked(bool(self._card.get("foil")))
        form.addRow("", self._foil_cb)

        # Container
        self._container_cb = QComboBox()
        self._container_cb.addItem("(no container)", None)
        for c in self._containers:
            self._container_cb.addItem(c["name"], c["id"])
        cur_cid = self._card.get("container_id")
        for i in range(self._container_cb.count()):
            if self._container_cb.itemData(i) == cur_cid:
                self._container_cb.setCurrentIndex(i)
                break
        form.addRow("Container:", self._container_cb)

        root.addLayout(form)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._on_save)

    def _on_save(self):
        changes = {
            "condition":    self._condition_cb.currentText(),
            "language":     self._lang_cb.currentData(),
            "foil":         1 if self._foil_cb.isChecked() else 0,
            "container_id": self._container_cb.currentData(),
        }
        self.saved.emit(changes)
        self.accept()
