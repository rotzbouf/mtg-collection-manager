"""Add Card page — unified Scryfall lookup (name, set, collector number, language)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QLineEdit, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem,
    QFrame, QScrollArea, QSizePolicy, QMessageBox,
    QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from qasync import asyncSlot

from desktop.utils import CONDITIONS, display_name, lang_flag, format_price, scale_pixmap, async_pixmap
from desktop.widgets.card_detail import ManaWidget

_LANGUAGES = [
    ("en",  "English"),
    ("de",  "German"),
    ("fr",  "French"),
    ("it",  "Italian"),
    ("es",  "Spanish"),
    ("pt",  "Portuguese"),
    ("ja",  "Japanese"),
    ("ko",  "Korean"),
    ("ru",  "Russian"),
    ("zhs", "Simplified Chinese"),
    ("zht", "Traditional Chinese"),
    ("ph",  "Phyrexian"),
]

_PLACEHOLDER_STYLE = "background: #1e1e2e; border-radius: 8px; color: #555; font-size: 12px;"


class AddCardWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: list[dict] = []
        self._selected_card: Optional[dict] = None
        self._containers: list[dict] = []
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_containers)

    def refresh(self):
        self._load_containers()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_search_pane())
        splitter.addWidget(self._build_preview_pane())
        splitter.setSizes([380, 800])

        root.addWidget(splitter)

        self._search_lang_cb.currentIndexChanged.connect(self._update_lang_label)
        self._update_lang_label()

    # ---- Search pane (left) ------------------------------------------ #

    def _build_search_pane(self) -> QWidget:
        w = QWidget()
        w.setMaximumWidth(420)
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        layout.addWidget(QLabel("<h2>Add Card</h2>"))

        hint = QLabel(
            "Fill in any combination of fields. "
            "Set + collector number gives a direct lookup; "
            "name (partial or full) triggers a search."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)

        # ---- Fields ----
        layout.addWidget(QLabel("Name (full or partial):"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Lightning Bolt  or  Blitz")
        self._name_edit.setClearButtonEnabled(True)
        layout.addWidget(self._name_edit)

        # Set + collector number side by side
        sc_row = QHBoxLayout()
        sc_row.setSpacing(8)

        set_col = QVBoxLayout()
        set_col.setSpacing(3)
        set_col.addWidget(QLabel("Set code:"))
        self._set_edit = QLineEdit()
        self._set_edit.setPlaceholderText("e.g. mh2")
        set_col.addWidget(self._set_edit)
        sc_row.addLayout(set_col)

        cn_col = QVBoxLayout()
        cn_col.setSpacing(3)
        cn_col.addWidget(QLabel("Collector no.:"))
        self._cn_edit = QLineEdit()
        self._cn_edit.setPlaceholderText("e.g. 227")
        cn_col.addWidget(self._cn_edit)
        sc_row.addLayout(cn_col)

        layout.addLayout(sc_row)

        layout.addWidget(QLabel("Language:"))
        self._search_lang_cb = QComboBox()
        for code, label in _LANGUAGES:
            self._search_lang_cb.addItem(label, code)
        layout.addWidget(self._search_lang_cb)

        self._search_btn = QPushButton("Search")
        self._search_btn.setDefault(True)
        layout.addWidget(self._search_btn)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep2)

        # Log area
        log_hdr = QHBoxLayout()
        log_hdr.addWidget(QLabel("<b>Log</b>"))
        log_hdr.addStretch()
        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(50)
        clear_btn.setStyleSheet("font-size: 11px; padding: 2px 6px;")
        log_hdr.addWidget(clear_btn)
        layout.addLayout(log_hdr)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(80)
        self._log_view.setStyleSheet(
            "font-size: 11px; font-family: monospace; background: #0d1117; color: #8b949e;"
        )
        self._log_view.setPlaceholderText("Search activity will appear here…")
        layout.addWidget(self._log_view)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep3)

        layout.addWidget(QLabel("<b>Results</b>"))
        self._results_list = QListWidget()
        self._results_list.setAlternatingRowColors(True)
        self._results_list.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._results_list, stretch=1)

        # Signals
        self._search_btn.clicked.connect(self._on_search)
        self._name_edit.returnPressed.connect(self._on_search)
        self._set_edit.returnPressed.connect(self._on_search)
        self._cn_edit.returnPressed.connect(self._on_search)
        self._results_list.currentRowChanged.connect(self._on_result_selected)
        clear_btn.clicked.connect(self._log_view.clear)

        return w

    # ---- Preview pane (right) ---------------------------------------- #

    def _build_preview_pane(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)

        self._empty_lbl = QLabel("Search for a card\nto see a preview here.")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet("color: #444; font-size: 15px;")
        self._empty_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._empty_lbl)

        self._preview_widget = QWidget()
        self._preview_widget.setVisible(False)
        preview_layout = QHBoxLayout(self._preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(20)

        # Card image
        self._img_label = QLabel()
        self._img_label.setFixedSize(280, 390)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet(_PLACEHOLDER_STYLE)
        preview_layout.addWidget(self._img_label, alignment=Qt.AlignmentFlag.AlignTop)

        # Right column: info + collection fields
        right = QVBoxLayout()
        right.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(280)
        info_w = QWidget()
        info_layout = QVBoxLayout(info_w)
        info_layout.setSpacing(3)
        info_layout.setContentsMargins(0, 0, 8, 0)

        def _row(label: str) -> QLabel:
            hdr = QLabel(f"<b>{label}</b>")
            hdr.setStyleSheet("font-size: 11px; color: #888;")
            lbl = QLabel()
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 13px;")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info_layout.addWidget(hdr)
            info_layout.addWidget(lbl)
            return lbl

        self._lbl_name  = _row("Name")
        self._lbl_set   = _row("Set")
        self._lbl_type  = _row("Type")

        mana_hdr = QLabel("<b>Mana cost</b>")
        mana_hdr.setStyleSheet("font-size: 11px; color: #888;")
        info_layout.addWidget(mana_hdr)
        self._mana_widget = ManaWidget()
        info_layout.addWidget(self._mana_widget)

        self._lbl_price = _row("Price")
        info_layout.addStretch()
        scroll.setWidget(info_w)
        right.addWidget(scroll)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        right.addWidget(sep)

        right.addWidget(QLabel("<b>Collection details</b>"))

        cl_row = QHBoxLayout()
        cond_col = QVBoxLayout()
        cond_col.addWidget(QLabel("Condition:"))
        self._condition_cb = QComboBox()
        self._condition_cb.addItems(CONDITIONS)
        cond_col.addWidget(self._condition_cb)
        cl_row.addLayout(cond_col)

        lang_col = QVBoxLayout()
        lang_col.addWidget(QLabel("Language:"))
        self._lang_lbl = QLabel()
        self._lang_lbl.setStyleSheet(
            "font-size: 13px; color: #aaa; padding: 3px 0; font-style: italic;"
        )
        self._lang_lbl.setToolTip("Matches the search language selected on the left")
        lang_col.addWidget(self._lang_lbl)
        cl_row.addLayout(lang_col)
        right.addLayout(cl_row)

        self._foil_cb = QCheckBox("✦ Foil")
        self._foil_cb.setStyleSheet("""
            QCheckBox {
                font-size: 13px;
                font-weight: bold;
                color: #d4af37;
                padding: 4px 0;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        right.addWidget(self._foil_cb)

        right.addWidget(QLabel("Container:"))
        container_row = QHBoxLayout()
        self._container_cb = QComboBox()
        self._container_cb.addItem("(no container)", None)
        container_row.addWidget(self._container_cb, stretch=1)
        self._new_container_btn = QPushButton("+ New")
        self._new_container_btn.setToolTip("Create new container")
        self._new_container_btn.setStyleSheet(
            "font-size: 11px; padding: 4px 8px;"
            " background: #0f3460; color: white; border-radius: 3px;"
        )
        container_row.addWidget(self._new_container_btn)
        right.addLayout(container_row)

        right.addSpacing(8)
        self._add_btn = QPushButton("Add to Collection")
        self._add_btn.setStyleSheet(
            "font-size: 14px; padding: 10px; background: #0f3460; color: white; border-radius: 4px;"
        )
        right.addWidget(self._add_btn)

        self._add_status = QLabel("")
        self._add_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._add_status.setStyleSheet("font-size: 12px;")
        right.addWidget(self._add_status)
        right.addStretch()

        preview_layout.addLayout(right, stretch=1)
        layout.addWidget(self._preview_widget)

        self._add_btn.clicked.connect(self._on_add)
        self._new_container_btn.clicked.connect(self._on_new_container)
        return w

    # ------------------------------------------------------------------ #
    # Helpers                                                               #
    # ------------------------------------------------------------------ #

    def _update_lang_label(self):
        self._lang_lbl.setText(self._search_lang_cb.currentText())

    def _log(self, msg: str):
        self._log_view.appendPlainText(msg)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def _populate_results(self, cards: list[dict]):
        self._results = cards
        self._results_list.clear()
        for card in cards:
            dname    = display_name(card)
            set_info = f"{(card.get('set_code') or '').upper()} #{card.get('collector_number') or ''}"
            flag     = lang_flag(card)
            price    = format_price(card.get("price_eur"))
            self._results_list.addItem(QListWidgetItem(f"{dname}\n{set_info}  {flag}  {price}"))
        if len(cards) == 1:
            self._results_list.setCurrentRow(0)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db

        self._containers = await db.list_containers()
        self._container_cb.blockSignals(True)
        current_data = self._container_cb.currentData()
        self._container_cb.clear()
        self._container_cb.addItem("(no container)", None)
        for c in self._containers:
            self._container_cb.addItem(c["name"], c["id"])
        for i in range(self._container_cb.count()):
            if self._container_cb.itemData(i) == current_data:
                self._container_cb.setCurrentIndex(i)
                break
        self._container_cb.blockSignals(False)

    def _on_new_container(self):
        from desktop.dialogs.container_dialog import ContainerDialog
        dlg = ContainerDialog(mode="create", parent=self)
        dlg.confirmed.connect(self._do_create_container)
        dlg.exec()

    @asyncSlot(str, str)
    async def _do_create_container(self, name: str, ctype: str):
        from desktop.db import db
        try:
            new_id = await db.create_container(name, type=ctype)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not create container:\n{exc}")
            return
        await self._load_containers()
        for i in range(self._container_cb.count()):
            if self._container_cb.itemData(i) == new_id:
                self._container_cb.setCurrentIndex(i)
                break

    # ------------------------------------------------------------------ #
    # Slots                                                                 #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_search(self):
        from desktop.db import scryfall

        name     = self._name_edit.text().strip()
        set_code = self._set_edit.text().strip() or None
        cn       = self._cn_edit.text().strip() or None
        lang     = self._search_lang_cb.currentData()

        if not name and not set_code:
            self._log("⚠ Enter at least a name or a set code.")
            return

        self._search_btn.setEnabled(False)
        self._results_list.clear()
        self._results = []

        # Direct lookup when both set + collector number are given
        if set_code and cn:
            effective_lang = lang or "en"
            self._log(f"→ Direct lookup: {set_code.upper()} #{cn} ({effective_lang})")
            try:
                card = await scryfall.get_by_collector(set_code.lower(), cn, effective_lang)
            except Exception as exc:
                self._log(f"✗ Exception: {exc}")
                self._search_btn.setEnabled(True)
                return
            if card is None and effective_lang != "en":
                # No print in this language — fall back to English edition
                self._log(f"✗ Not found in '{effective_lang}' — retrying as English…")
                try:
                    card = await scryfall.get_by_collector(set_code.lower(), cn, "en")
                except Exception as exc:
                    self._log(f"✗ Fallback exception: {exc}")
                    self._search_btn.setEnabled(True)
                    return
                if card is not None:
                    card["language"] = effective_lang
                    self._log(f"✓ Found via English fallback (language kept as '{effective_lang}').")
            self._search_btn.setEnabled(True)
            if card is None:
                self._log("✗ Not found (404 or API error).")
            else:
                cname = display_name(card)
                self._log(f"✓ {cname} ({(card.get('set_code') or '').upper()} #{card.get('collector_number')})")
                self._populate_results([card])
            return

        # Full-text / filter search
        parts = []
        if name:
            parts.append(f'"{name}"')
        if set_code:
            parts.append(f"set:{set_code}")
        if lang:
            parts.append(f"lang:{lang}")
        self._log(f"→ Searching: {' '.join(parts)}")

        try:
            cards = await scryfall.search_cards(name=name, set_code=set_code, lang=lang)
        except Exception as exc:
            self._log(f"✗ Error: {exc}")
            self._search_btn.setEnabled(True)
            return

        self._search_btn.setEnabled(True)
        if not cards and lang and lang != "en":
            # Language-filtered search returned nothing — fall back to English
            # search so the card data can still be retrieved.  The user's chosen
            # language is preserved: it is stamped onto the card when adding.
            self._log(f"✗ No results in '{lang}' — retrying without language filter…")
            try:
                cards = await scryfall.search_cards(name=name, set_code=set_code, lang=None)
            except Exception as exc:
                self._log(f"✗ Fallback error: {exc}")
                return
            if cards:
                # Mark each result with the user's chosen language so the
                # preview and add-flow carry the right language value.
                for c in cards:
                    c["language"] = lang
                self._log(f"✓ {len(cards)} result(s) via English fallback (language kept as '{lang}').")
            else:
                self._log("✗ No results found.")
        elif not cards:
            self._log("✗ No results found.")
        else:
            self._log(f"✓ {len(cards)} result(s).")
        if cards:
            self._populate_results(cards)

    def _on_result_selected(self, row: int):
        if row < 0 or row >= len(self._results):
            return
        self._show_card(self._results[row])

    def _show_card(self, card: dict):
        self._selected_card = card
        self._add_status.setText("")

        self._lbl_name.setText(f"<b>{display_name(card)}</b>")
        self._lbl_set.setText(
            f"{card.get('set_name', '')} "
            f"({(card.get('set_code') or '').upper()}) "
            f"#{card.get('collector_number', '')}"
        )
        self._lbl_type.setText(card.get("type_line") or "")
        self._mana_widget.set_mana(card.get("mana_cost") or "")
        eur = card.get("price_eur")
        usd = card.get("price_usd")
        price_parts = []
        if eur:
            price_parts.append(f"€{eur:.2f}")
        if usd:
            price_parts.append(f"${usd:.2f}")
        self._lbl_price.setText("  /  ".join(price_parts) or "—")

        self._empty_lbl.setVisible(False)
        self._preview_widget.setVisible(True)
        self._img_label.setText("Loading…")
        self._img_label.setPixmap(QPixmap())
        self._load_image(card)

    @asyncSlot()
    async def _load_image(self, card: dict):
        pixmap = await async_pixmap(card.get("scryfall_id"), card.get("image_url"))
        if self._selected_card and self._selected_card.get("scryfall_id") == card.get("scryfall_id"):
            if pixmap:
                self._img_label.setPixmap(scale_pixmap(pixmap, 280, 390))
                self._img_label.setText("")
            else:
                self._img_label.setText("No image\navailable")

    @asyncSlot()
    async def _on_add(self):
        if self._selected_card is None:
            return

        from desktop.db import db

        card = dict(self._selected_card)
        card["condition"]   = self._condition_cb.currentText()
        card["language"]    = self._search_lang_cb.currentData()
        card["foil"]        = 1 if self._foil_cb.isChecked() else 0
        card["container_id"]= self._container_cb.currentData()
        card["quantity"]    = 1

        try:
            new_id = await db.add_card(card, added_by="desktop")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not add card:\n{exc}")
            return

        name = display_name(self._selected_card)
        container_id = card["container_id"]

        # Build confirmation line
        parts = [f"✓ '{name}' added  (ID {new_id})"]
        if container_id is not None:
            count = await db.count_cards(container_id=container_id)
            container_name = self._container_cb.currentText()
            parts.append(f"{container_name}: {count} cards")

        self._add_status.setText("   ·   ".join(parts))
        self._add_status.setStyleSheet("font-size: 12px; color: #4caf50;")
        QTimer.singleShot(6000, lambda: self._add_status.setText(""))
