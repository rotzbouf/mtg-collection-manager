"""Reusable card detail panel (right pane)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from qasync import asyncSlot

from desktop.utils import display_name, lang_flag, format_price, scale_pixmap, async_pixmap

_PLACEHOLDER_STYLE = "background: #1e1e2e; border-radius: 8px; color: #555; font-size: 12px;"


class CardDetailPanel(QWidget):
    """Card detail panel with image and metadata.

    Emits edit_requested / delete_requested with the card dict.
    """

    edit_requested = pyqtSignal(dict)
    delete_requested = pyqtSignal(dict)

    def __init__(self, parent=None, show_buttons: bool = True):
        super().__init__(parent)
        self._current_card: Optional[dict] = None
        self._show_buttons = show_buttons
        self._build_ui()
        self.clear()

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    def set_card(self, card: dict):
        self._current_card = card
        self._load_card(card)

    def clear(self):
        self._current_card = None
        self._img_label.setText("Select a card\nto view details")
        self._img_label.setPixmap(QPixmap())
        for lbl in (
            self._lbl_name, self._lbl_set, self._lbl_type,
            self._lbl_oracle, self._lbl_mana, self._lbl_cmc,
            self._lbl_pt, self._lbl_price_eur, self._lbl_price_usd,
            self._lbl_lang, self._lbl_cond,
        ):
            lbl.setText("")
        if self._show_buttons:
            self._edit_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Image
        self._img_label = QLabel()
        self._img_label.setFixedSize(223, 310)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet(_PLACEHOLDER_STYLE)
        root.addWidget(self._img_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Scrollable info area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        inner = QWidget()
        info = QVBoxLayout(inner)
        info.setSpacing(3)
        info.setContentsMargins(4, 4, 4, 4)

        def _row(label: str) -> QLabel:
            lbl = QLabel()
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info.addWidget(QLabel(f"<small><b>{label}</b></small>"))
            info.addWidget(lbl)
            return lbl

        self._lbl_name = _row("Name")
        self._lbl_set = _row("Set")
        self._lbl_type = _row("Type")
        self._lbl_mana = _row("Mana cost")
        self._lbl_cmc = _row("CMC")
        self._lbl_oracle = _row("Oracle text")
        self._lbl_pt = _row("P / T / Loyalty")
        self._lbl_lang = _row("Language")
        self._lbl_cond = _row("Condition")
        self._lbl_price_eur = _row("Price (EUR)")
        self._lbl_price_usd = _row("Price (USD)")

        info.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        if self._show_buttons:
            btn_row = QHBoxLayout()
            self._edit_btn = QPushButton("Edit")
            self._delete_btn = QPushButton("Delete")
            self._delete_btn.setStyleSheet("color: #e05c5c;")
            self._edit_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            btn_row.addWidget(self._edit_btn)
            btn_row.addWidget(self._delete_btn)
            root.addLayout(btn_row)

            self._edit_btn.clicked.connect(self._on_edit)
            self._delete_btn.clicked.connect(self._on_delete)

    # ------------------------------------------------------------------ #
    # Internals                                                             #
    # ------------------------------------------------------------------ #

    def _load_card(self, card: dict):
        self._lbl_name.setText(f"<b>{display_name(card)}</b>")
        self._lbl_set.setText(
            f"{card.get('set_name', '')} "
            f"({(card.get('set_code') or '').upper()}) "
            f"#{card.get('collector_number', '')}"
        )
        self._lbl_type.setText(card.get("type_line") or "")
        self._lbl_mana.setText(card.get("mana_cost") or "—")
        cmc = card.get("cmc")
        self._lbl_cmc.setText(str(int(cmc)) if cmc is not None else "—")
        oracle = card.get("oracle_text") or ""
        self._lbl_oracle.setText(oracle[:600] + ("…" if len(oracle) > 600 else ""))

        pt_parts = []
        if card.get("power") is not None:
            pt_parts.append(f"{card['power']}/{card.get('toughness', '?')}")
        if card.get("loyalty"):
            pt_parts.append(f"Loyalty: {card['loyalty']}")
        self._lbl_pt.setText("  ".join(pt_parts) or "—")

        lang = (card.get("language") or "en").upper()
        flag = lang_flag(card)
        self._lbl_lang.setText(f"{flag} ({lang})")
        self._lbl_cond.setText(card.get("condition") or "")
        self._lbl_price_eur.setText(format_price(card.get("price_eur")))
        usd = card.get("price_usd")
        self._lbl_price_usd.setText(f"${float(usd):.2f}" if usd else "—")

        if self._show_buttons:
            self._edit_btn.setEnabled(True)
            self._delete_btn.setEnabled(True)

        # Load image
        self._img_label.setText("Loading…")
        self._img_label.setPixmap(QPixmap())
        self._load_image(card)

    @asyncSlot()
    async def _load_image(self, card: dict):
        scryfall_id = card.get("scryfall_id")
        image_url = card.get("image_url")
        pixmap = await async_pixmap(scryfall_id, image_url)
        # Make sure the card hasn't changed while we were loading
        if self._current_card and self._current_card.get("id") == card.get("id"):
            if pixmap:
                self._img_label.setPixmap(scale_pixmap(pixmap))
                self._img_label.setText("")
            else:
                self._img_label.setText("No image\navailable")

    def _on_edit(self):
        if self._current_card:
            self.edit_requested.emit(self._current_card)

    def _on_delete(self):
        if self._current_card:
            self.delete_requested.emit(self._current_card)
