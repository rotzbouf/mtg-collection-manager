"""Reusable card detail panel (right pane)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from qasync import asyncSlot

from desktop.utils import display_name, lang_flag, format_price, scale_pixmap, async_pixmap

_PLACEHOLDER_STYLE = "background: #1e1e2e; border-radius: 8px; color: #555; font-size: 12px;"

_MANA_DIR = Path(__file__).parent.parent.parent / "images" / "mana"
_MANA_DIR.mkdir(parents=True, exist_ok=True)
_MANA_ICON_SIZE = 22  # px


def _parse_mana(mana_str: str) -> list[str]:
    """'{2}{W}{G/U}' → ['2', 'W', 'GU']"""
    return [s.replace("/", "") for s in re.findall(r"\{([^}]+)\}", mana_str)]


def _symbol_url(symbol: str) -> str:
    return f"https://svgs.scryfall.io/card-symbols/{symbol}.svg"


async def _symbol_pixmap(symbol: str) -> Optional[QPixmap]:
    path = _MANA_DIR / f"{symbol}.svg"
    if not path.exists():
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(_symbol_url(symbol), timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        path.write_bytes(await r.read())
                    else:
                        return None
        except Exception:
            return None
    try:
        renderer = QSvgRenderer(str(path))
        pixmap = QPixmap(_MANA_ICON_SIZE, _MANA_ICON_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        return pixmap
    except Exception:
        return None


class ManaWidget(QWidget):
    """Displays a mana cost string as a row of SVG icons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(3)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._mana_str = ""

    def set_mana(self, mana_str: str):
        self._mana_str = mana_str
        self._reload()

    def clear_mana(self):
        self._mana_str = ""
        self._clear_icons()

    def _clear_icons(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    @asyncSlot()
    async def _reload(self):
        self._clear_icons()
        symbols = _parse_mana(self._mana_str)
        if not symbols:
            lbl = QLabel("—")
            lbl.setStyleSheet("font-size: 13px;")
            self._layout.addWidget(lbl)
            return
        for symbol in symbols:
            pixmap = await _symbol_pixmap(symbol)
            lbl = QLabel()
            if pixmap:
                lbl.setPixmap(pixmap)
                lbl.setFixedSize(_MANA_ICON_SIZE, _MANA_ICON_SIZE)
                lbl.setToolTip(f"{{{symbol}}}")
            else:
                lbl.setText(f"{{{symbol}}}")
                lbl.setStyleSheet("font-size: 12px;")
            self._layout.addWidget(lbl)


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
            self._lbl_oracle, self._lbl_cmc,
            self._lbl_pt, self._lbl_price_eur, self._lbl_cm_price, self._lbl_price_usd,
            self._lbl_lang, self._lbl_cond,
        ):
            lbl.setText("")
        self._mana_widget.clear_mana()
        self._price_history_btn.setEnabled(False)
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
        self._img_label.setFixedSize(280, 390)
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
            lbl.setStyleSheet("font-size: 13px;")
            header = QLabel(f"<b>{label}</b>")
            header.setStyleSheet("font-size: 11px; color: #888;")
            info.addWidget(header)
            info.addWidget(lbl)
            return lbl

        self._lbl_name = _row("Name")
        self._lbl_set = _row("Set")
        self._lbl_type = _row("Type")

        # Mana cost — icon row
        mana_header = QLabel("<b>Mana cost</b>")
        mana_header.setStyleSheet("font-size: 11px; color: #888;")
        info.addWidget(mana_header)
        self._mana_widget = ManaWidget()
        info.addWidget(self._mana_widget)

        self._lbl_cmc = _row("Mana Value")
        self._lbl_oracle = _row("Oracle text")
        self._lbl_pt = _row("P / T / Loyalty")
        self._lbl_lang = _row("Language")
        self._lbl_cond = _row("Condition")
        self._lbl_price_eur = _row("Price (EUR)")
        self._lbl_cm_price = _row("CM Trend (EUR)")
        self._lbl_price_usd = _row("Price (USD)")

        info.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        self._price_history_btn = QPushButton("Price History")
        self._price_history_btn.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        self._price_history_btn.setEnabled(False)
        self._price_history_btn.clicked.connect(self._on_price_history)
        root.addWidget(self._price_history_btn)

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
        self._mana_widget.set_mana(card.get("mana_cost") or "")
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
        cm_trend = card.get("cm_trend")
        self._lbl_cm_price.setText(f"€{float(cm_trend):.2f}" if cm_trend else "—")
        usd = card.get("price_usd")
        self._lbl_price_usd.setText(f"${float(usd):.2f}" if usd else "—")

        self._price_history_btn.setEnabled(bool(card.get("scryfall_id")))
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

    def _on_price_history(self):
        if self._current_card:
            from desktop.dialogs.price_history import PriceHistoryDialog
            dlg = PriceHistoryDialog(self._current_card, parent=self)
            dlg.exec()

    def _on_edit(self):
        if self._current_card:
            self.edit_requested.emit(self._current_card)

    def _on_delete(self):
        if self._current_card:
            self.delete_requested.emit(self._current_card)
