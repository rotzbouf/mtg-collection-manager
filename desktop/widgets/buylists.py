"""Buylists widget — paste or fetch a store buylist, match against collection."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton,
    QTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QSplitter, QFrame, QComboBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from qasync import asyncSlot

_DEFAULT_URL = "https://mtgkartenankauf.de/buylist.html"

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


# ── HTML table parser ──────────────────────────────────────────────────────────

class _TableParser(HTMLParser):
    """Extract all <table> contents as list[list[str]] rows."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._cur_row: list[str] = []
        self._cur_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self.tables.append([])
        elif tag in ("tr",) and self._in_table:
            self._cur_row = []
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = True
            self._cur_cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
        elif tag == "tr" and self._in_table and self._cur_row:
            self.tables[-1].append(self._cur_row)
            self._cur_row = []
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = False
            self._cur_row.append(" ".join(self._cur_cell).strip())
            self._cur_cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell.append(data.strip())


# ── Buylist parsing ────────────────────────────────────────────────────────────

_PRICE_RE = re.compile(r"(\d+[.,]\d+)")


def _parse_price(text: str) -> Optional[float]:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _is_header_row(row: list[str]) -> bool:
    """Heuristic: row is a header if no cell looks like a price and no cell is empty."""
    if not row:
        return False
    first = row[0].lower()
    return any(kw in first for kw in ("name", "karte", "card", "edition", "set", "preis", "price"))


def parse_buylist_text(text: str) -> list[dict]:
    """Parse pasted or fetched buylist text into [{name, price, set}] entries.

    Handles:
    - Tab-separated browser table copies (name \\t price, or name \\t set \\t price)
    - HTML from URL fetch (tables with data-name=buylist-table or any table)
    - Plain one-card-per-line lists
    """
    text = text.strip()
    if not text:
        return []

    # ── HTML path ─────────────────────────────────────────────────────────────
    if text.lstrip().startswith("<"):
        return _parse_html_buylist(text)

    # ── Tab/comma separated ───────────────────────────────────────────────────
    lines = [l for l in text.splitlines() if l.strip()]
    entries: list[dict] = []
    for line in lines:
        # Try tab-separated first, then comma
        if "\t" in line:
            parts = [p.strip() for p in line.split("\t")]
        else:
            parts = [p.strip() for p in line.split(",")]

        if not parts or not parts[0]:
            continue

        # Skip obvious header rows
        if _is_header_row(parts):
            continue

        name = parts[0]
        price: Optional[float] = None
        set_code: Optional[str] = None

        # Find price: last column that looks like a decimal number
        for p in reversed(parts[1:]):
            price = _parse_price(p)
            if price is not None:
                break

        # Set: second column if it looks like a set code/name and isn't the price
        if len(parts) >= 3:
            set_code = parts[1] if not _parse_price(parts[1]) else None

        entries.append({"name": name, "price": price, "set": set_code})

    return entries


def _parse_html_buylist(html: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)

    entries: list[dict] = []
    for table in parser.tables:
        for row in table:
            if not row or not row[0]:
                continue
            if _is_header_row(row):
                continue

            name = row[0]
            price: Optional[float] = None
            set_code: Optional[str] = None

            for cell in reversed(row[1:]):
                price = _parse_price(cell)
                if price is not None:
                    break
            if len(row) >= 3:
                set_code = row[1] if not _parse_price(row[1]) else None

            entries.append({"name": name, "price": price, "set": set_code})

    return entries


# ── Card image preview ────────────────────────────────────────────────────────

_IMG_W = 200
_IMG_H = 280


class _CardPreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(_IMG_W + 24)
        self.setMaximumWidth(_IMG_W + 40)
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 12, 8, 8)
        lay.setSpacing(6)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._img_lbl = QLabel("Karte\nauswählen")
        self._img_lbl.setFixedSize(_IMG_W, _IMG_H)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet(
            "background: #1e1e2e; border-radius: 10px; color: #555; font-size: 11px;"
        )
        lay.addWidget(self._img_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._name_lbl = QLabel()
        self._name_lbl.setWordWrap(True)
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_lbl.setStyleSheet("font-size: 11px; font-weight: bold; padding-top: 6px;")
        lay.addWidget(self._name_lbl)

        self._info_lbl = QLabel()
        self._info_lbl.setWordWrap(True)
        self._info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_lbl.setStyleSheet("font-size: 10px; color: #aaa;")
        lay.addWidget(self._info_lbl)

        lay.addStretch()

    def clear(self):
        self._img_lbl.clear()
        self._img_lbl.setText("Karte\nauswählen")
        self._name_lbl.clear()
        self._info_lbl.clear()

    def show_card(self, match: dict):
        self._name_lbl.setText(match.get("name", ""))
        bl  = match.get("bl_price")
        mkt = match.get("mkt_price")
        parts = []
        if bl  is not None: parts.append(f"Buylist:  €{bl:.2f}")
        if mkt is not None: parts.append(f"Markt:    €{mkt:.2f}")
        parts.append(f"Anzahl:  {match.get('count', 0)}")
        ct = match.get("container", "")
        if ct and ct != "—":
            parts.append(ct)
        self._info_lbl.setText("\n".join(parts))
        self._img_lbl.clear()
        self._img_lbl.setText("⋯")

    def set_image(self, pixmap):
        if pixmap:
            scaled = pixmap.scaled(
                _IMG_W, _IMG_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setText("")
        else:
            self._img_lbl.clear()
            self._img_lbl.setText("Kein Bild")


# ── Widget ─────────────────────────────────────────────────────────────────────

class BuylistsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_ready = False
        self._entries: list[dict] = []   # parsed buylist
        self._matches: list[dict] = []   # matched collection rows with buylist info
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(QLabel("<h2>Buylists</h2>"))

        # ── Saved sources ─────────────────────────────────────────────────────
        sources_row = QHBoxLayout()
        sources_row.addWidget(QLabel("Saved source:"))
        self._sources_combo = QComboBox()
        self._sources_combo.setMinimumWidth(220)
        sources_row.addWidget(self._sources_combo)
        self._refresh_sources_btn = QPushButton("↻")
        self._refresh_sources_btn.setFixedWidth(32)
        self._refresh_sources_btn.setToolTip("Reload saved sources from settings")
        sources_row.addWidget(self._refresh_sources_btn)
        sources_row.addStretch()
        root.addLayout(sources_row)
        self._load_sources()

        # ── URL row ───────────────────────────────────────────────────────────
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL:"))
        self._url_edit = QLineEdit(_DEFAULT_URL)
        url_row.addWidget(self._url_edit)
        self._fetch_btn = QPushButton("Fetch")
        self._fetch_btn.setFixedWidth(80)
        url_row.addWidget(self._fetch_btn)
        root.addLayout(url_row)

        # ── Paste area ────────────────────────────────────────────────────────
        sep = QLabel("— or paste buylist content below —")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sep.setStyleSheet("color: #585b70; font-size: 11px;")
        root.addWidget(sep)

        self._paste_area = QTextEdit()
        self._paste_area.setPlaceholderText(
            "Paste the buylist here (tab-separated from browser copy, or HTML).\n\n"
            "Expected format: Card Name <tab> Price  (one card per line)\n"
            "Example:\n"
            "  Lightning Bolt\t0.30\n"
            "  Sol Ring\t2.50"
        )
        self._paste_area.setMaximumHeight(160)
        self._paste_area.setFont(QFont("Monospace", 9))
        root.addWidget(self._paste_area)

        # ── Action row ────────────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._match_btn = QPushButton("Match collection")
        self._match_btn.setEnabled(False)
        action_row.addWidget(self._match_btn)
        action_row.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #888; font-size: 11px;")
        action_row.addWidget(self._status_lbl)
        root.addLayout(action_row)

        # ── Summary bar ───────────────────────────────────────────────────────
        self._summary_frame = QFrame()
        self._summary_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._summary_frame.setStyleSheet(
            "QFrame { background: #1e1e2e; border: 1px solid #313244; border-radius: 4px; padding: 4px; }"
        )
        summary_layout = QHBoxLayout(self._summary_frame)
        summary_layout.setContentsMargins(8, 4, 8, 4)
        self._sum_buylist_lbl  = QLabel("Buylist: — cards")
        self._sum_matches_lbl  = QLabel("Matches: —")
        self._sum_bl_val_lbl   = QLabel("Buylist value: —")
        self._sum_mkt_val_lbl  = QLabel("Market value: —")
        for lbl in (self._sum_buylist_lbl, self._sum_matches_lbl,
                    self._sum_bl_val_lbl, self._sum_mkt_val_lbl):
            lbl.setStyleSheet("color: #cdd6f4; font-size: 11px; padding: 0 8px;")
            summary_layout.addWidget(lbl)
        summary_layout.addStretch()
        root.addWidget(self._summary_frame)

        # ── Results table ─────────────────────────────────────────────────────
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "Card name", "Set", "Buylist €", "Market €", "Count", "Container",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(1, 80)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 90)
        self._table.setColumnWidth(4, 55)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)

        self._preview = _CardPreview()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._table)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        root.addWidget(splitter)

        # ── Signals ───────────────────────────────────────────────────────────
        self._fetch_btn.clicked.connect(self._on_fetch)
        self._match_btn.clicked.connect(self._on_match)
        self._paste_area.textChanged.connect(self._on_paste_changed)
        self._sources_combo.currentIndexChanged.connect(self._on_source_selected)
        self._refresh_sources_btn.clicked.connect(self._load_sources)
        self._table.itemSelectionChanged.connect(self._on_row_selected)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def db_ready(self):
        self._db_ready = True
        self._match_btn.setEnabled(True)

    def refresh(self):
        self._load_sources()

    # ── Source helpers ────────────────────────────────────────────────────────

    def _load_sources(self):
        import core.config as _cfg
        sources = _cfg.load().get("buylist_sources", [])
        self._sources_combo.blockSignals(True)
        self._sources_combo.clear()
        self._sources_combo.addItem("— select saved source —", "")
        for src in sources:
            name = src.get("name") or src.get("url", "")
            url  = src.get("url", "")
            self._sources_combo.addItem(name, url)
        self._sources_combo.blockSignals(False)

    def _on_source_selected(self, index: int):
        url = self._sources_combo.itemData(index)
        if url:
            self._url_edit.setText(url)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_paste_changed(self):
        has_text = bool(self._paste_area.toPlainText().strip())
        if self._db_ready:
            self._match_btn.setEnabled(has_text or bool(self._entries))

    @asyncSlot()
    async def _on_fetch(self):
        import aiohttp
        url = self._url_edit.text().strip()
        if not url:
            return

        self._fetch_btn.setEnabled(False)
        self._status_lbl.setText("Fetching…")

        try:
            jar = aiohttp.CookieJar(unsafe=True)
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(
                headers=_FETCH_HEADERS,
                cookie_jar=jar,
                timeout=timeout,
            ) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    html = await resp.text(errors="replace")

            if len(html) < 500:
                self._status_lbl.setText("Fetch failed — site may require JavaScript. Paste manually.")
                return

            self._paste_area.setPlainText(html)
            self._status_lbl.setText(f"Fetched {len(html):,} bytes — click 'Match collection'")
        except Exception as exc:
            self._status_lbl.setText(f"Fetch error: {exc} — paste manually instead")
        finally:
            self._fetch_btn.setEnabled(True)

    @asyncSlot()
    async def _on_match(self):
        from desktop.db import db

        text = self._paste_area.toPlainText().strip()
        if not text:
            self._status_lbl.setText("Nothing to parse.")
            return

        self._match_btn.setEnabled(False)
        self._status_lbl.setText("Parsing buylist…")

        self._entries = parse_buylist_text(text)
        if not self._entries:
            self._status_lbl.setText("No entries found. Check the pasted format.")
            self._match_btn.setEnabled(True)
            return

        self._status_lbl.setText(f"{len(self._entries)} entries parsed, searching collection…")

        # Build lookup: lower(name) → buylist entry
        buylist_by_name: dict[str, dict] = {}
        for e in self._entries:
            buylist_by_name[e["name"].lower()] = e

        card_names = list(buylist_by_name.keys())
        collection_cards = await db.get_cards_by_names(card_names)

        # Group collection rows by normalised name, attach buylist info
        grouped: dict[str, dict] = {}
        for card in collection_cards:
            key = self._resolve_key(card, buylist_by_name)
            if key is None:
                continue
            if key not in grouped:
                grouped[key] = {
                    "name":      card.get("name_en") or card.get("printed_name") or key,
                    "set_code":  buylist_by_name[key].get("set") or card.get("set_code") or "",
                    "bl_price":  buylist_by_name[key].get("price"),
                    "mkt_price": card.get("price_eur"),
                    "count":     0,
                    "container": card.get("container_name") or "—",
                    "_cards":    [],
                }
            grouped[key]["count"] += 1
            grouped[key]["_cards"].append(card)
            # Use highest market price among copies
            mkt = card.get("price_eur") or 0.0
            if mkt > (grouped[key]["mkt_price"] or 0.0):
                grouped[key]["mkt_price"] = mkt
            # Collect unique container names
            ct = card.get("container_name") or "—"
            existing = grouped[key]["container"]
            if ct not in existing:
                grouped[key]["container"] = f"{existing}, {ct}" if existing != "—" else ct

        self._matches = list(grouped.values())
        self._render_table()
        self._update_summary()
        self._match_btn.setEnabled(True)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_key(card: dict, buylist: dict[str, dict]) -> Optional[str]:
        for field in ("name_en", "name_de", "printed_name"):
            v = (card.get(field) or "").lower()
            if v and v in buylist:
                return v
        return None

    def _render_table(self):
        self._table.blockSignals(True)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._matches))

        for row_idx, m in enumerate(self._matches):
            bl_price  = m["bl_price"]
            mkt_price = m["mkt_price"]

            name_item = QTableWidgetItem(m["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, row_idx)
            set_item  = QTableWidgetItem(str(m["set_code"] or ""))
            bl_item   = _numeric_item(bl_price)
            mkt_item  = _numeric_item(mkt_price)
            cnt_item  = _numeric_item(m["count"], decimals=0)
            ct_item   = QTableWidgetItem(m["container"])

            # Colour buylist price red if lower than market, green if higher
            if bl_price is not None and mkt_price is not None:
                if bl_price >= mkt_price * 0.8:
                    bl_item.setForeground(QColor("#a6e3a1"))  # green — good deal
                else:
                    bl_item.setForeground(QColor("#f38ba8"))  # red — low offer

            for col, item in enumerate((name_item, set_item, bl_item,
                                        mkt_item, cnt_item, ct_item)):
                self._table.setItem(row_idx, col, item)

        self._table.setSortingEnabled(True)
        self._table.blockSignals(False)
        # Default sort: buylist price descending
        self._table.sortItems(2, Qt.SortOrder.DescendingOrder)
        self._preview.clear()

    def _update_summary(self):
        n_bl   = len(self._entries)
        n_hit  = len(self._matches)
        total_bl  = sum((m["bl_price"]  or 0) * m["count"] for m in self._matches)
        total_mkt = sum((m["mkt_price"] or 0) * m["count"] for m in self._matches)

        self._sum_buylist_lbl.setText(f"Buylist: {n_bl} cards")
        self._sum_matches_lbl.setText(f"Matches: {n_hit}")
        self._sum_bl_val_lbl.setText(f"Buylist value: €{total_bl:.2f}")
        self._sum_mkt_val_lbl.setText(f"Market value: €{total_mkt:.2f}")
        self._status_lbl.setText(f"Done — {n_hit} of {n_bl} buylist cards found in collection")

    @asyncSlot()
    async def _on_row_selected(self):
        from desktop.utils import async_pixmap

        row = self._table.currentRow()
        if row < 0:
            self._preview.clear()
            return

        name_item = self._table.item(row, 0)
        if name_item is None:
            self._preview.clear()
            return

        match_idx = name_item.data(Qt.ItemDataRole.UserRole)
        if match_idx is None or match_idx >= len(self._matches):
            self._preview.clear()
            return

        match = self._matches[match_idx]
        self._preview.show_card(match)

        cards = match.get("_cards", [])
        if not cards:
            self._preview.set_image(None)
            return

        card = cards[0]
        pixmap = await async_pixmap(card.get("scryfall_id"), card.get("image_url"))
        self._preview.set_image(pixmap)


def _numeric_item(value, decimals: int = 2) -> QTableWidgetItem:
    """QTableWidgetItem that sorts numerically."""
    if value is None:
        item = QTableWidgetItem("—")
        item.setData(Qt.ItemDataRole.UserRole, -1.0)
    else:
        text = str(int(value)) if decimals == 0 else f"€{value:.{decimals}f}"
        item = QTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, float(value))
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item
