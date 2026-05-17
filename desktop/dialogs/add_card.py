"""Add card dialog: Scryfall lookup + confirm."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QMessageBox, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from qasync import asyncSlot

from desktop.utils import CONDITIONS, display_name, scale_pixmap, async_pixmap


class AddCardDialog(QDialog):
    """Two-step dialog: (1) lookup card on Scryfall, (2) confirm details."""

    card_confirmed = pyqtSignal(dict)  # emitted with the final card dict

    def __init__(self, parent=None, containers: list[dict] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Card")
        self.setMinimumWidth(520)
        self._card_data: dict | None = None
        self._containers = containers or []

        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)

        # --- Search row ---
        search_row = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Card name (English or German)…")
        self._set_edit = QLineEdit()
        self._set_edit.setPlaceholderText("Set code (optional)")
        self._set_edit.setMaximumWidth(80)
        self._search_btn = QPushButton("Look up")
        self._search_btn.setDefault(True)
        search_row.addWidget(QLabel("Name:"))
        search_row.addWidget(self._name_edit, stretch=3)
        search_row.addWidget(QLabel("Set:"))
        search_row.addWidget(self._set_edit)
        search_row.addWidget(self._search_btn)
        root.addLayout(search_row)

        # --- Status label ---
        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._status)

        # --- Card preview area (hidden until found) ---
        self._preview_frame = QFrame()
        self._preview_frame.setFrameShape(QFrame.Shape.StyledPanel)
        preview_layout = QHBoxLayout(self._preview_frame)

        # Image
        self._img_label = QLabel()
        self._img_label.setFixedSize(150, 209)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("background: #2a2a2a; border-radius: 4px;")
        preview_layout.addWidget(self._img_label)

        # Info + collection fields
        info_form = QFormLayout()
        self._lbl_name = QLabel()
        self._lbl_name.setWordWrap(True)
        self._lbl_set = QLabel()
        self._lbl_type = QLabel()
        self._lbl_type.setWordWrap(True)
        self._lbl_mana = QLabel()
        self._lbl_price = QLabel()

        info_form.addRow("Name:", self._lbl_name)
        info_form.addRow("Set:", self._lbl_set)
        info_form.addRow("Type:", self._lbl_type)
        info_form.addRow("Mana:", self._lbl_mana)
        info_form.addRow("Price:", self._lbl_price)

        # Collection metadata
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)

        self._condition_cb = QComboBox()
        self._condition_cb.addItems(CONDITIONS)

        self._lang_cb = QComboBox()
        for code, label in [
            ("en", "English"), ("de", "German"), ("fr", "French"),
            ("it", "Italian"), ("es", "Spanish"), ("pt", "Portuguese"),
            ("ja", "Japanese"), ("ko", "Korean"), ("ru", "Russian"),
            ("zhs", "Simplified Chinese"), ("zht", "Traditional Chinese"),
        ]:
            self._lang_cb.addItem(label, code)

        self._foil_cb = QCheckBox("Foil")

        self._container_cb = QComboBox()
        self._container_cb.addItem("(no container)", None)
        for c in self._containers:
            self._container_cb.addItem(c["name"], c["id"])

        info_form.addRow("", sep)
        info_form.addRow("Condition:", self._condition_cb)
        info_form.addRow("Language:", self._lang_cb)
        info_form.addRow("", self._foil_cb)
        info_form.addRow("Container:", self._container_cb)

        info_widget_container = QFrame()
        info_widget_container.setLayout(info_form)
        preview_layout.addWidget(info_widget_container, stretch=1)
        self._preview_frame.setVisible(False)
        root.addWidget(self._preview_frame)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel_btn = QPushButton("Cancel")
        self._add_btn = QPushButton("Add to collection")
        self._add_btn.setEnabled(False)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._add_btn)
        root.addLayout(btn_row)

        # --- Connections ---
        self._search_btn.clicked.connect(self._on_search)
        self._name_edit.returnPressed.connect(self._on_search)
        self._cancel_btn.clicked.connect(self.reject)
        self._add_btn.clicked.connect(self._on_add)

    # ------------------------------------------------------------------ #
    # Slots                                                                 #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_search(self):
        from desktop.db import scryfall

        name = self._name_edit.text().strip()
        if not name:
            return

        self._search_btn.setEnabled(False)
        self._status.setText("Searching Scryfall…")
        self._add_btn.setEnabled(False)
        self._preview_frame.setVisible(False)

        set_code = self._set_edit.text().strip() or None
        try:
            card, lang = await scryfall.resolve_card(name, set_code=set_code)
        except Exception as exc:
            self._status.setText(f"Error: {exc}")
            self._search_btn.setEnabled(True)
            return

        self._search_btn.setEnabled(True)

        if card is None:
            self._status.setText("Card not found on Scryfall.")
            return

        self._card_data = card
        self._status.setText("")

        # Populate info labels
        self._lbl_name.setText(display_name(card))
        self._lbl_set.setText(
            f"{card.get('set_name', '')} ({(card.get('set_code') or '').upper()}) "
            f"#{card.get('collector_number', '')}"
        )
        self._lbl_type.setText(card.get("type_line") or "")
        self._lbl_mana.setText(card.get("mana_cost") or "—")
        eur = card.get("price_eur")
        usd = card.get("price_usd")
        self._lbl_price.setText(
            f"€{eur:.2f}" if eur else ("—")
            + (f"  /  ${usd:.2f}" if usd else "")
        )

        # Set language selector to detected language
        for i in range(self._lang_cb.count()):
            if self._lang_cb.itemData(i) == lang:
                self._lang_cb.setCurrentIndex(i)
                break

        self._preview_frame.setVisible(True)
        self._add_btn.setEnabled(True)

        # Load image asynchronously
        self._img_label.setText("Loading…")
        pixmap = await async_pixmap(card.get("scryfall_id"), card.get("image_url"))
        if pixmap:
            self._img_label.setPixmap(scale_pixmap(pixmap, 150, 209))
            self._img_label.setText("")
        else:
            self._img_label.setText("No image")

    def _on_add(self):
        if self._card_data is None:
            return
        card = dict(self._card_data)
        card["condition"] = self._condition_cb.currentText()
        card["language"] = self._lang_cb.currentData()
        card["foil"] = 1 if self._foil_cb.isChecked() else 0
        card["container_id"] = self._container_cb.currentData()
        card["quantity"] = 1
        self.card_confirmed.emit(card)
        self.accept()
