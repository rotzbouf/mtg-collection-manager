"""Create / rename container dialog."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton,
)
from PyQt6.QtCore import pyqtSignal


CONTAINER_TYPES = ["binder", "box", "deck", "wishlist", "other"]


class ContainerDialog(QDialog):
    """Dialog for creating a new container or renaming an existing one."""

    confirmed = pyqtSignal(str, str)  # (name, type) — type is '' on rename

    def __init__(
        self,
        mode: str = "create",  # 'create' | 'rename'
        current_name: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._mode = mode
        self.setWindowTitle("New Container" if mode == "create" else "Rename Container")
        self.setMinimumWidth(340)
        self._build_ui(current_name)

    def _build_ui(self, current_name: str):
        root = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(current_name)
        self._name_edit.setPlaceholderText("Container name…")
        form.addRow("Name:", self._name_edit)

        self._type_cb = QComboBox()
        self._type_cb.addItems(CONTAINER_TYPES)
        if self._mode == "create":
            form.addRow("Type:", self._type_cb)

        root.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        ok_btn = QPushButton("Create" if self._mode == "create" else "Rename")
        ok_btn.setDefault(True)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        root.addLayout(btn_row)

        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(self._on_ok)
        self._name_edit.returnPressed.connect(self._on_ok)

    def _on_ok(self):
        name = self._name_edit.text().strip()
        if not name:
            return
        ctype = self._type_cb.currentText() if self._mode == "create" else ""
        self.confirmed.emit(name, ctype)
        self.accept()
