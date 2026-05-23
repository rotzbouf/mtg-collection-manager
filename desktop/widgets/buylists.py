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
    QTabWidget, QProgressBar, QSpinBox,
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
        self._entries: list[dict] = []   # parsed buylist (manual tab)
        self._matches: list[dict] = []   # matched rows (manual tab)
        self._search_results: list[dict] = []  # per-store results (search tab)
        self._selected_store_matches: list[dict] = []  # shown in detail table
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(QLabel("<h2>Buylists</h2>"))

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_manual_tab(), "Manual")
        self._tabs.addTab(self._build_search_tab(), "Web Search")
        root.addWidget(self._tabs)

    # ── Manual tab ────────────────────────────────────────────────────────────

    def _build_manual_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

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
        self._paste_area.setMaximumHeight(130)
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

        # ── Results table + preview ───────────────────────────────────────────
        self._table = _make_card_table()
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

        return tab

    # ── Web Search tab ────────────────────────────────────────────────────────

    def _build_search_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        # ── Controls ──────────────────────────────────────────────────────────
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Keyword:"))
        self._search_kw_combo = QComboBox()
        self._search_kw_combo.setEditable(True)
        self._search_kw_combo.setMinimumWidth(280)
        ctrl_row.addWidget(self._search_kw_combo, stretch=1)

        ctrl_row.addWidget(QLabel("Max results:"))
        self._search_max_sb = QSpinBox()
        self._search_max_sb.setRange(1, 20)
        self._search_max_sb.setValue(10)
        self._search_max_sb.setFixedWidth(55)
        ctrl_row.addWidget(self._search_max_sb)

        self._search_btn = QPushButton("Search & Match")
        self._search_btn.setEnabled(False)
        ctrl_row.addWidget(self._search_btn)
        root.addLayout(ctrl_row)

        # ── Progress ──────────────────────────────────────────────────────────
        self._search_progress = QProgressBar()
        self._search_progress.setVisible(False)
        self._search_progress.setTextVisible(True)
        root.addWidget(self._search_progress)

        self._search_status = QLabel("")
        self._search_status.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self._search_status)

        # ── Splitter: store ranking | card detail ─────────────────────────────
        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: store ranking
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 4, 0)
        left_lay.setSpacing(4)
        left_lay.addWidget(QLabel("<b>Store Ranking</b>  (by total buylist value)"))

        self._store_table = QTableWidget(0, 5)
        self._store_table.setHorizontalHeaderLabels([
            "Store", "Matches", "BL Total €", "MKT Total €", "Above Market",
        ])
        sh = self._store_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        sh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        sh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self._store_table.setColumnWidth(1, 65)
        self._store_table.setColumnWidth(2, 85)
        self._store_table.setColumnWidth(3, 85)
        self._store_table.setColumnWidth(4, 100)
        self._store_table.setAlternatingRowColors(True)
        self._store_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._store_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._store_table.setSortingEnabled(True)
        self._store_table.verticalHeader().setVisible(False)
        left_lay.addWidget(self._store_table)

        h_splitter.addWidget(left)

        # Right: card detail for selected store
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)
        right_lay.setSpacing(4)

        self._detail_header = QLabel("Select a store to view card matches")
        self._detail_header.setStyleSheet("color: #888; font-size: 11px;")
        right_lay.addWidget(self._detail_header)

        self._detail_table = _make_card_table()
        self._detail_preview = _CardPreview()

        detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        detail_splitter.addWidget(self._detail_table)
        detail_splitter.addWidget(self._detail_preview)
        detail_splitter.setStretchFactor(0, 1)
        detail_splitter.setStretchFactor(1, 0)
        right_lay.addWidget(detail_splitter)

        h_splitter.addWidget(right)
        h_splitter.setStretchFactor(0, 1)
        h_splitter.setStretchFactor(1, 2)
        root.addWidget(h_splitter)

        # ── Signals ───────────────────────────────────────────────────────────
        self._search_btn.clicked.connect(self._on_search)
        self._store_table.itemSelectionChanged.connect(self._on_store_selected)
        self._detail_table.itemSelectionChanged.connect(self._on_detail_row_selected)

        self._load_search_keywords()
        return tab

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def db_ready(self):
        self._db_ready = True
        self._match_btn.setEnabled(True)
        self._load_search_keywords()

    def refresh(self):
        self._load_sources()
        self._load_search_keywords()

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

    def _load_search_keywords(self):
        import core.config as _cfg
        brave = _cfg.load().get("brave", {})
        api_key = brave.get("api_key", "")
        keywords = brave.get("keywords", [])
        max_res = brave.get("max_results", 10)
        self._search_kw_combo.blockSignals(True)
        current = self._search_kw_combo.currentText()
        self._search_kw_combo.clear()
        for kw in keywords:
            self._search_kw_combo.addItem(kw)
        if current and self._search_kw_combo.findText(current) == -1:
            self._search_kw_combo.addItem(current)
        if current:
            idx = self._search_kw_combo.findText(current)
            if idx >= 0:
                self._search_kw_combo.setCurrentIndex(idx)
        self._search_kw_combo.blockSignals(False)
        self._search_max_sb.setValue(max_res)
        self._search_btn.setEnabled(bool(api_key) and self._db_ready)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_paste_changed(self):
        has_text = bool(self._paste_area.toPlainText().strip())
        if self._db_ready:
            self._match_btn.setEnabled(has_text or bool(self._entries))

    # ── Web search slots ──────────────────────────────────────────────────────

    @asyncSlot()
    async def _on_search(self):
        import core.config as _cfg
        from core.brave_search import search_buylist_urls
        from desktop.db import db
        import aiohttp

        brave = _cfg.load().get("brave", {})
        api_key = brave.get("api_key", "").strip()
        if not api_key:
            self._search_status.setText("No Brave API key — set it in Settings → Buylists.")
            return

        query = self._search_kw_combo.currentText().strip()
        if not query:
            self._search_status.setText("Enter a search keyword.")
            return

        max_res = self._search_max_sb.value()
        self._search_btn.setEnabled(False)
        self._search_status.setText("Searching…")
        self._search_progress.setVisible(True)
        self._search_progress.setRange(0, 0)
        self._search_results = []
        self._store_table.setRowCount(0)
        self._detail_table.setRowCount(0)
        self._detail_header.setText("Select a store to view card matches")

        try:
            urls = await search_buylist_urls(api_key, query, max_res)
        except Exception as exc:
            self._search_status.setText(f"Search error: {exc}")
            self._search_progress.setVisible(False)
            self._search_btn.setEnabled(True)
            return

        if not urls:
            self._search_status.setText("No results found.")
            self._search_progress.setVisible(False)
            self._search_btn.setEnabled(True)
            return

        self._search_progress.setRange(0, len(urls))
        self._search_progress.setValue(0)

        store_results: list[dict] = []
        for i, hit in enumerate(urls):
            url   = hit["url"]
            title = hit["title"] or url
            self._search_status.setText(f"Fetching {i+1}/{len(urls)}: {title[:60]}…")
            self._search_progress.setValue(i)

            html = await self._fetch_url_silent(url)
            if html is None:
                store_results.append({
                    "title": title, "url": url, "status": "fetch_error",
                    "entries": [], "matches": [],
                    "total_bl": 0.0, "total_mkt": 0.0, "above_market": 0,
                })
                continue

            entries = parse_buylist_text(html)
            if not entries:
                store_results.append({
                    "title": title, "url": url, "status": "no_entries",
                    "entries": [], "matches": [],
                    "total_bl": 0.0, "total_mkt": 0.0, "above_market": 0,
                })
                continue

            buylist_by_name = {e["name"].lower(): e for e in entries}
            card_names = list(buylist_by_name.keys())
            try:
                collection_cards = await db.get_cards_by_names(
                    card_names, exclude_container_types=["deck", "commander"]
                )
            except Exception:
                collection_cards = []

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
                mkt = card.get("price_eur") or 0.0
                if mkt > (grouped[key]["mkt_price"] or 0.0):
                    grouped[key]["mkt_price"] = mkt
                ct = card.get("container_name") or "—"
                existing = grouped[key]["container"]
                if ct not in existing:
                    grouped[key]["container"] = f"{existing}, {ct}" if existing != "—" else ct

            matches = list(grouped.values())
            total_bl  = sum((m["bl_price"]  or 0) * m["count"] for m in matches)
            total_mkt = sum((m["mkt_price"] or 0) * m["count"] for m in matches)
            above = sum(
                1 for m in matches
                if m["bl_price"] is not None and m["mkt_price"] is not None
                and m["bl_price"] >= m["mkt_price"] * 0.8
            )
            store_results.append({
                "title": title, "url": url, "status": "ok",
                "entries": entries, "matches": matches,
                "total_bl": total_bl, "total_mkt": total_mkt, "above_market": above,
            })

        self._search_progress.setValue(len(urls))
        self._search_progress.setVisible(False)

        # Sort by total buylist value descending (profit ranking)
        store_results.sort(key=lambda s: s["total_bl"], reverse=True)
        self._search_results = store_results

        ok = [s for s in store_results if s["status"] == "ok"]
        self._search_status.setText(
            f"Done — {len(ok)}/{len(store_results)} stores matched · "
            f"Best: {ok[0]['title'][:40] if ok else '—'}"
        )
        self._render_store_table()
        self._search_btn.setEnabled(True)

    async def _fetch_url_silent(self, url: str) -> Optional[str]:
        import aiohttp
        try:
            jar = aiohttp.CookieJar(unsafe=True)
            async with aiohttp.ClientSession(
                headers=_FETCH_HEADERS, cookie_jar=jar,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as s:
                async with s.get(url, allow_redirects=True) as resp:
                    html = await resp.text(errors="replace")
            return html if len(html) >= 300 else None
        except Exception:
            return None

    def _render_store_table(self):
        self._store_table.blockSignals(True)
        self._store_table.setSortingEnabled(False)
        self._store_table.setRowCount(0)
        self._store_table.setRowCount(len(self._search_results))

        for row_idx, s in enumerate(self._search_results):
            ok = s["status"] == "ok"
            name_item = QTableWidgetItem(s["title"])
            name_item.setData(Qt.ItemDataRole.UserRole, row_idx)
            if not ok:
                name_item.setForeground(QColor("#585b70"))
                name_item.setToolTip(f"{s['url']}\nStatus: {s['status']}")
            else:
                name_item.setToolTip(s["url"])

            match_item = _numeric_item(len(s["matches"]) if ok else 0, decimals=0)
            bl_item    = _numeric_item(s["total_bl"]  if ok else None)
            mkt_item   = _numeric_item(s["total_mkt"] if ok else None)
            above_item = _numeric_item(s["above_market"] if ok else 0, decimals=0)

            if ok and s["above_market"] > 0:
                above_item.setForeground(QColor("#a6e3a1"))

            self._store_table.setItem(row_idx, 0, name_item)
            self._store_table.setItem(row_idx, 1, match_item)
            self._store_table.setItem(row_idx, 2, bl_item)
            self._store_table.setItem(row_idx, 3, mkt_item)
            self._store_table.setItem(row_idx, 4, above_item)

        self._store_table.setSortingEnabled(True)
        self._store_table.blockSignals(False)

    def _on_store_selected(self):
        row = self._store_table.currentRow()
        if row < 0:
            return
        name_item = self._store_table.item(row, 0)
        if name_item is None:
            return
        store_idx = name_item.data(Qt.ItemDataRole.UserRole)
        if store_idx is None or store_idx >= len(self._search_results):
            return

        store = self._search_results[store_idx]
        self._selected_store_matches = store.get("matches", [])
        self._detail_header.setText(
            f"{store['title']}  —  {len(self._selected_store_matches)} matches  |  "
            f"BL: €{store['total_bl']:.2f}  MKT: €{store['total_mkt']:.2f}  "
            f"Above market: {store['above_market']}"
        )
        _render_card_table(self._detail_table, self._selected_store_matches)

    @asyncSlot()
    async def _on_detail_row_selected(self):
        from desktop.utils import async_pixmap

        row = self._detail_table.currentRow()
        if row < 0:
            self._detail_preview.clear()
            return
        name_item = self._detail_table.item(row, 0)
        if name_item is None:
            return
        match_idx = name_item.data(Qt.ItemDataRole.UserRole)
        if match_idx is None or match_idx >= len(self._selected_store_matches):
            return
        match = self._selected_store_matches[match_idx]
        self._detail_preview.show_card(match)
        cards = match.get("_cards", [])
        if cards:
            pixmap = await async_pixmap(cards[0].get("scryfall_id"), cards[0].get("image_url"))
            self._detail_preview.set_image(pixmap)

    # ── Manual tab slots ──────────────────────────────────────────────────────

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
        collection_cards = await db.get_cards_by_names(
            card_names, exclude_container_types=["deck", "commander"]
        )

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
        _render_card_table(self._table, self._matches)
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


def _make_card_table() -> QTableWidget:
    t = QTableWidget(0, 6)
    t.setHorizontalHeaderLabels(["Card name", "Set", "Buylist €", "Market €", "Count", "Container"])
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
    t.setColumnWidth(1, 80)
    t.setColumnWidth(2, 90)
    t.setColumnWidth(3, 90)
    t.setColumnWidth(4, 55)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setSortingEnabled(True)
    t.verticalHeader().setVisible(False)
    return t


def _render_card_table(table: QTableWidget, matches: list[dict]) -> None:
    table.blockSignals(True)
    table.setSortingEnabled(False)
    table.setRowCount(0)
    table.setRowCount(len(matches))
    for row_idx, m in enumerate(matches):
        bl_price  = m["bl_price"]
        mkt_price = m["mkt_price"]
        name_item = QTableWidgetItem(m["name"])
        name_item.setData(Qt.ItemDataRole.UserRole, row_idx)
        bl_item  = _numeric_item(bl_price)
        mkt_item = _numeric_item(mkt_price)
        if bl_price is not None and mkt_price is not None:
            if bl_price >= mkt_price * 0.8:
                bl_item.setForeground(QColor("#a6e3a1"))
            else:
                bl_item.setForeground(QColor("#f38ba8"))
        for col, item in enumerate((
            name_item,
            QTableWidgetItem(str(m.get("set_code") or "")),
            bl_item,
            mkt_item,
            _numeric_item(m["count"], decimals=0),
            QTableWidgetItem(m.get("container") or "—"),
        )):
            table.setItem(row_idx, col, item)
    table.setSortingEnabled(True)
    table.blockSignals(False)
    table.sortItems(2, Qt.SortOrder.DescendingOrder)


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
