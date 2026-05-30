"""Deck Improvement tab — propose meta-backed card swaps for a deck.

For each non-land card in the selected deck that scores poorly against the
competitive meta (via meta_card_scores), we find the best type-matched
replacement from the user's binder/box cards.  Accepting a swap physically
moves the cards between containers (deck ↔ binder).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QSplitter, QMessageBox, QAbstractItemView, QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPixmap
from qasync import asyncSlot

from desktop.utils import display_name, async_pixmap
from core.i18n import _

logger = logging.getLogger(__name__)


def _bg(coro):
    """Schedule a coroutine as a fire-and-forget task with error logging."""
    task = asyncio.ensure_future(coro)
    task.add_done_callback(
        lambda f: logger.error("Background task failed: %s", f.exception())
        if not f.cancelled() and f.exception() else None
    )
    return task


# ── Format mapping ─────────────────────────────────────────────────────────────

_DECK_TO_META: dict[str, str] = {
    "commander": "EDH",
    "vintage":   "VI",
    "legacy":    "LE",
    "modern":    "MO",
    "standard":  "ST",
    "pioneer":   "PI",
    "pauper":    "PAU",
}

_META_LABELS: dict[str, str] = {
    "EDH": "Commander (EDH)",
    "VI":  "Vintage",
    "LE":  "Legacy",
    "MO":  "Modern",
    "ST":  "Standard",
    "PI":  "Pioneer",
    "PAU": "Pauper",
}

# ── Type-group helpers ─────────────────────────────────────────────────────────

# (English token, localized synonyms) — enough to correctly classify
# German type lines without pulling in a full localization table.
_TYPE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Creature",     ("Kreatur",)),
    ("Planeswalker", ()),
    ("Instant",      ("Spontanzauber",)),
    ("Sorcery",      ("Hexerei",)),
    ("Enchantment",  ("Verzauberung",)),
    ("Artifact",     ("Artefakt",)),
)


def _type_key(card: dict) -> str:
    tl = card.get("type_line") or ""
    for token, synonyms in _TYPE_TOKENS:
        if token in tl or any(s in tl for s in synonyms):
            return token
    return "Other"


def _color_identity(card: dict) -> set[str]:
    raw = card.get("color_identity") or "[]"
    try:
        ci = json.loads(raw) if isinstance(raw, str) else list(raw)
        return set(ci)
    except Exception:
        return set()


def _fits_deck_colors(card: dict, deck_colors: set[str]) -> bool:
    """True when every color in card's identity is in deck_colors (colorless always fits)."""
    ci = _color_identity(card)
    return not ci or ci <= deck_colors


# ── Proposal engine ────────────────────────────────────────────────────────────

def _build_proposals(
    deck_cards: list[dict],
    candidates: list[dict],
    deck_color_identity: set[str],
    filter_colors: bool,
) -> list[dict]:
    """Return swap proposals sorted by meta score delta (best first).

    Each proposal:
        deck_card   – card currently in the deck (to remove)
        candidate   – card from the collection (to add)
        delta       – candidate.meta_score − deck_card.meta_score
        tier        – 1 (strong) or 2 (moderate)
    """
    # Names already in the deck – candidates with the same name are skipped.
    deck_names: set[str] = {(c.get("name_en") or "").lower() for c in deck_cards}

    # Build per-type candidate pools (dedup by name, sorted best-first).
    cand_by_type: dict[str, list[dict]] = {}
    seen_names: set[str] = set()

    for cand in sorted(candidates, key=lambda c: -(c.get("meta_score") or 0)):
        name_lo = (cand.get("name_en") or "").lower()
        if name_lo in deck_names or name_lo in seen_names:
            continue
        if filter_colors and not _fits_deck_colors(cand, deck_color_identity):
            continue
        tk = _type_key(cand)
        cand_by_type.setdefault(tk, []).append(cand)
        seen_names.add(name_lo)

    proposals: list[dict] = []
    used_cand_names: set[str] = set()

    # Iterate deck cards weakest-first so the rarest gains go first.
    for deck_card in sorted(deck_cards, key=lambda c: c.get("meta_score", 0)):
        tk = _type_key(deck_card)
        pool = cand_by_type.get(tk, [])
        for cand in pool:
            cname = (cand.get("name_en") or "").lower()
            if cname in used_cand_names:
                continue
            delta = (cand.get("meta_score") or 0.0) - (deck_card.get("meta_score") or 0.0)
            if delta <= 0:
                break  # pool is sorted descending – nothing better follows
            deck_score = deck_card.get("meta_score") or 0.0
            cand_score = cand.get("meta_score") or 0.0
            tier = 1 if deck_score == 0 and cand_score >= 5 else 2
            proposals.append({
                "deck_card": deck_card,
                "candidate": cand,
                "delta": delta,
                "tier": tier,
            })
            used_cand_names.add(cname)
            break  # one proposal per deck card

    proposals.sort(key=lambda p: -p["delta"])
    return proposals


# ── Compact card preview panel ─────────────────────────────────────────────────

_IMG_W, _IMG_H = 200, 279   # ~71 % of standard 280×390 — card art stays readable


class _CompactCardPanel(QWidget):
    """Minimal card panel: image + name + set/collector-number + location.

    Designed for the swap view where the user needs a quick visual reference
    and the collector number to physically locate the card.
    """

    _PLACEHOLDER = "color: #7f849c; border: 1px solid #333; border-radius: 6px;"

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._current_card: dict | None = None
        self._build_ui(title)
        self.clear()

    def _build_ui(self, title: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # Section title
        self._title_lbl = QLabel(title)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setStyleSheet("font-size: 11px; color: #a6adc8;")
        root.addWidget(self._title_lbl)

        # Card name
        self._name_lbl = QLabel()
        self._name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_lbl.setWordWrap(True)
        self._name_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #e0e0e0;")
        root.addWidget(self._name_lbl)

        # Card image
        self._img_lbl = QLabel()
        self._img_lbl.setFixedSize(_IMG_W, _IMG_H)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet(self._PLACEHOLDER)
        root.addWidget(self._img_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Set + collector number — most important reference line
        self._set_lbl = QLabel()
        self._set_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._set_lbl.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #90caf9; letter-spacing: 1px;"
        )
        root.addWidget(self._set_lbl)

        # Location / container
        self._loc_lbl = QLabel()
        self._loc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loc_lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        root.addWidget(self._loc_lbl)

        root.addStretch()

    # ------------------------------------------------------------------ #

    def set_card(self, card: dict):
        self._current_card = card
        self._name_lbl.setText(display_name(card))

        set_code = (card.get("set_code") or "").upper()
        cnum     = card.get("collector_number") or "?"
        set_name = card.get("set_name") or ""
        self._set_lbl.setText(f"{set_code} #{cnum}")
        self._set_lbl.setToolTip(set_name)

        loc = card.get("container_name") or ""
        self._loc_lbl.setText(f"📦 {loc}" if loc else "")

        self._img_lbl.setText("…")
        self._img_lbl.setPixmap(QPixmap())
        _bg(self._load_image(card))

    def clear(self):
        self._current_card = None
        self._name_lbl.setText("")
        self._set_lbl.setText("")
        self._loc_lbl.setText("")
        self._img_lbl.setText("")
        self._img_lbl.setPixmap(QPixmap())

    async def _load_image(self, card: dict):
        scryfall_id = card.get("scryfall_id")
        if not scryfall_id:
            self._img_lbl.setText(_("No image"))
            return
        try:
            pixmap = await async_pixmap(scryfall_id, card.get("image_url"))
        except Exception:
            self._img_lbl.setText(_("No image"))
            return
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                _IMG_W, _IMG_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setText("")
        else:
            self._img_lbl.setText(_("No image"))


# ── Widget ─────────────────────────────────────────────────────────────────────

_COL_REMOVE  = 0
_COL_SET_R   = 1   # set + collector# of the card to remove
_COL_TYPE_R  = 2
_COL_SCORE_R = 3
_COL_ARROW   = 4
_COL_ADD     = 5
_COL_SET_A   = 6   # set + collector# of the swap-in candidate
_COL_LOC     = 7
_COL_SCORE_A = 8
_COL_DELTA   = 9
_COL_ACCEPT  = 10
_N_COLS      = 11

_TIER1_BG = QColor("#1e3a1e")   # strong improvement – dark green
_TIER2_BG = QColor("#1a1a2e")   # moderate – subtle blue-grey


class DeckImproveWidget(QWidget):
    """'Improve Deck' tab — meta-driven swap proposals with one-click execution."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._decks: list[dict] = []           # container rows
        self._proposals: list[dict] = []       # current proposal list
        self._loading = False
        self._build_ui()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def db_ready(self):
        QTimer.singleShot(0, self._load_decks)

    def refresh(self):
        _bg(self._async_load_decks())

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Header bar ────────────────────────────────────────────────── #
        header = QHBoxLayout()

        header.addWidget(QLabel(_("Deck:")))
        self._deck_combo = QComboBox()
        self._deck_combo.setMinimumWidth(200)
        self._deck_combo.currentIndexChanged.connect(self._on_deck_changed)
        header.addWidget(self._deck_combo)

        self._format_lbl = QLabel("")
        self._format_lbl.setStyleSheet("color: #a6adc8; font-size: 12px; margin-left: 8px;")
        header.addWidget(self._format_lbl)

        header.addStretch()

        self._filter_colors_chk = QCheckBox(_("Color identity filter"))
        self._filter_colors_chk.setChecked(True)
        self._filter_colors_chk.setToolTip(
            _("Only suggest cards whose color identity fits the deck")
        )
        self._filter_colors_chk.stateChanged.connect(self._on_filter_changed)
        header.addWidget(self._filter_colors_chk)

        self._refresh_btn = QPushButton("↻  " + _("Refresh"))
        self._refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(self._refresh_btn)

        root.addLayout(header)

        # ── Status label ──────────────────────────────────────────────── #
        self._status_lbl = QLabel(_("Select a deck to see improvement proposals."))
        self._status_lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        root.addWidget(self._status_lbl)

        # ── Main split: removal panel | table | candidate panel ──────────── #
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: compact panel for the card being removed
        self._remove_panel = _CompactCardPanel(_("Remove from deck"))
        splitter.addWidget(self._remove_panel)

        # Centre: proposals table
        self._table = QTableWidget(0, _N_COLS)
        self._table.setHorizontalHeaderLabels([
            _("Remove from deck"), _("Set / #"), _("Type"), _("Score"),
            "→",
            _("Swap in"), _("Set / #"), _("Location"), _("Score"), _("Δ Score"),
            "",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_REMOVE,  QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_SET_R,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_TYPE_R,  QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_SCORE_R, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ARROW,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ADD,     QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_SET_A,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_LOC,     QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_SCORE_A, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_DELTA,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ACCEPT,  QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._table)

        # Right: compact panel for the swap-in candidate
        self._candidate_panel = _CompactCardPanel(_("Swap in"))
        splitter.addWidget(self._candidate_panel)

        splitter.setStretchFactor(0, 1)   # removal panel
        splitter.setStretchFactor(1, 4)   # table
        splitter.setStretchFactor(2, 1)   # candidate panel

        root.addWidget(splitter, stretch=1)

        # ── Legend ────────────────────────────────────────────────────── #
        legend = QHBoxLayout()
        for color, text in [(_TIER1_BG, "■ " + _("Strong (score 0 → ≥5)")),
                             (_TIER2_BG, "■ " + _("Moderate (≥2× better)"))]:
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"background: {color.name()}; color: #ccc; font-size: 11px;"
                "padding: 2px 6px; border-radius: 3px;"
            )
            legend.addWidget(lbl)
        legend.addStretch()
        root.addLayout(legend)

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    def _load_decks(self):
        _bg(self._async_load_decks())

    @asyncSlot()
    async def _async_load_decks(self):
        from desktop.db import db
        try:
            containers = await db.list_containers()
        except Exception as exc:
            logger.error("DeckImprove: failed to load containers: %s", exc)
            return

        self._decks = [
            c for c in containers
            if c.get("type") in ("commander", "deck")
        ]

        prev_id = None
        if self._deck_combo.currentIndex() >= 0:
            prev_id = self._deck_combo.currentData()

        self._deck_combo.blockSignals(True)
        self._deck_combo.clear()
        for ct in self._decks:
            fmt = ct.get("deck_format") or ct.get("type") or "?"
            self._deck_combo.addItem(
                f"{ct['name']}  ({fmt})", userData=ct["id"]
            )

        # Restore previous selection if possible
        if prev_id is not None:
            for i in range(self._deck_combo.count()):
                if self._deck_combo.itemData(i) == prev_id:
                    self._deck_combo.setCurrentIndex(i)
                    break

        self._deck_combo.blockSignals(False)

        if self._deck_combo.count() > 0:
            self._on_deck_changed(self._deck_combo.currentIndex())

    def _on_deck_changed(self, _index: int):
        _bg(self._load_proposals())

    def _on_refresh(self):
        _bg(self._load_proposals())

    def _on_filter_changed(self, _state: int):
        self._populate_table()

    @asyncSlot()
    async def _load_proposals(self):
        if self._loading:
            return
        idx = self._deck_combo.currentIndex()
        if idx < 0:
            return

        container_id = self._deck_combo.currentData()
        deck_info = next((d for d in self._decks if d["id"] == container_id), None)
        if not deck_info:
            return

        deck_format = (deck_info.get("deck_format") or deck_info.get("type") or "").lower()
        meta_fmt = _DECK_TO_META.get(deck_format)

        if not meta_fmt:
            self._status_lbl.setText(
                _("⚠  Unknown format '{fmt}' — cannot map to meta data.").format(fmt=deck_format)
            )
            self._table.setRowCount(0)
            self._proposals = []
            return

        self._format_lbl.setText(_META_LABELS.get(meta_fmt, meta_fmt))
        self._status_lbl.setText(_("Loading…"))
        self._loading = True
        self._refresh_btn.setEnabled(False)

        try:
            from desktop.db import db
            deck_cards  = await db.get_deck_cards_for_improve(container_id, meta_fmt)
            candidates  = await db.get_binder_cards_for_improve(meta_fmt, container_id)
        except Exception as exc:
            logger.error("DeckImprove: query failed: %s", exc)
            self._status_lbl.setText(f"Error: {exc}")
            return
        finally:
            self._loading = False
            self._refresh_btn.setEnabled(True)

        if not deck_cards:
            self._status_lbl.setText(_("No (non-land) cards found in this deck."))
            self._table.setRowCount(0)
            self._proposals = []
            return

        if not candidates:
            self._status_lbl.setText(
                _("No binder/box cards have meta scores for {fmt}. "
                  "Try running a meta crawl first.").format(
                    fmt=_META_LABELS.get(meta_fmt, meta_fmt))
            )
            self._table.setRowCount(0)
            self._proposals = []
            return

        # Deck color identity = union of all card color identities
        deck_colors: set[str] = set()
        for card in deck_cards:
            deck_colors |= _color_identity(card)

        # Stash for the populate step (re-run on filter toggle without re-querying)
        self._pending_deck_cards  = deck_cards
        self._pending_candidates  = candidates
        self._pending_deck_colors = deck_colors

        self._populate_table()

    def _populate_table(self):
        deck_cards  = getattr(self, "_pending_deck_cards",  [])
        candidates  = getattr(self, "_pending_candidates",  [])
        deck_colors = getattr(self, "_pending_deck_colors", set())

        if not deck_cards:
            return

        filter_colors = self._filter_colors_chk.isChecked()
        proposals = _build_proposals(deck_cards, candidates, deck_colors, filter_colors)
        self._proposals = proposals

        total_deck_score  = sum(c.get("meta_score", 0) for c in deck_cards)
        total_gain        = sum(p["delta"] for p in proposals)

        if proposals:
            self._status_lbl.setText(
                f"{len(proposals)} proposals  ·  "
                f"Deck meta score: {total_deck_score:.1f}  ·  "
                f"Potential gain: +{total_gain:.1f}"
            )
        else:
            self._status_lbl.setText(
                _("No improvements found — your binder has no better-scoring cards of the same types.")
            )

        self._table.blockSignals(True)
        self._table.setRowCount(len(proposals))

        bold = QFont()
        bold.setBold(True)

        for row, prop in enumerate(proposals):
            dc   = prop["deck_card"]
            cand = prop["candidate"]
            tier = prop["tier"]
            bg   = _TIER1_BG if tier == 1 else _TIER2_BG

            def _item(text: str, align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter) -> QTableWidgetItem:
                item = QTableWidgetItem(str(text))
                item.setTextAlignment(align)
                item.setBackground(bg)
                return item

            center = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter

            def _set_ref(card: dict) -> str:
                code = (card.get("set_code") or "").upper()
                cnum = card.get("collector_number") or "?"
                return f"{code} #{cnum}"

            # Remove col
            remove_item = _item(display_name(dc))
            remove_item.setFont(bold)
            self._table.setItem(row, _COL_REMOVE,  remove_item)
            self._table.setItem(row, _COL_SET_R,   _item(_set_ref(dc), center))
            self._table.setItem(row, _COL_TYPE_R,  _item(_type_key(dc), center))
            self._table.setItem(row, _COL_SCORE_R, _item(f"{dc.get('meta_score', 0):.1f}", center))
            self._table.setItem(row, _COL_ARROW,   _item("→", center))

            # Add col
            add_name = display_name(cand)
            loc = cand.get("container_name") or "—"
            add_item = _item(add_name)
            add_item.setFont(bold)
            add_item.setForeground(QColor("#81c784"))
            self._table.setItem(row, _COL_ADD,     add_item)
            self._table.setItem(row, _COL_SET_A,   _item(_set_ref(cand), center))
            self._table.setItem(row, _COL_LOC,     _item(f"📦 {loc}", center))
            self._table.setItem(row, _COL_SCORE_A, _item(f"{cand.get('meta_score', 0):.1f}", center))
            self._table.setItem(row, _COL_DELTA,   _item(f"+{prop['delta']:.1f}", center))

            # Accept button (embedded as a push-button via a widget).
            # Capture *prop* by identity so the closure stays correct even
            # after other rows are removed and row indices shift.
            accept_btn = QPushButton(_("Accept"))
            accept_btn.setStyleSheet(
                "QPushButton { font-size: 11px; padding: 2px 8px; }"
                "QPushButton:hover { background: #2e7d32; }"
            )
            accept_btn.clicked.connect(lambda checked, p=prop: self._on_accept_prop(p))
            self._table.setCellWidget(row, _COL_ACCEPT, accept_btn)

        self._table.blockSignals(False)
        self._remove_panel.clear()
        self._candidate_panel.clear()

    # ------------------------------------------------------------------ #
    # Actions                                                               #
    # ------------------------------------------------------------------ #

    def _on_selection_changed(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._proposals):
            prop = self._proposals[row]
            self._remove_panel.set_card(prop["deck_card"])
            self._candidate_panel.set_card(prop["candidate"])
        else:
            self._remove_panel.clear()
            self._candidate_panel.clear()

    def _on_accept_prop(self, prop: dict):
        """Entry point from button click — find the live row for *prop* by identity."""
        row = next((i for i, p in enumerate(self._proposals) if p is prop), -1)
        if row >= 0:
            self._on_accept(row, prop)

    def _on_accept(self, row: int, prop: dict):
        dc   = prop["deck_card"]
        cand = prop["candidate"]

        deck_name  = display_name(dc)
        cand_name  = display_name(cand)
        cand_loc   = cand.get("container_name") or "unassigned"
        deck_loc   = dc.get("container_name") or "deck"

        reply = QMessageBox.question(
            self, _("Confirm swap"),
            _("Swap cards physically?\n\n"
              "  Remove:  {deck_name}\n"
              "    from:  {deck_loc}\n\n"
              "  Add:     {cand_name}\n"
              "    from:  {cand_loc}\n\n"
              "The two cards will exchange containers.").format(
                deck_name=deck_name, deck_loc=deck_loc,
                cand_name=cand_name, cand_loc=cand_loc),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        _bg(self._do_swap(prop, dc["id"], cand["id"]))

    @asyncSlot()
    async def _do_swap(self, prop: dict, card_id_deck: int, card_id_cand: int):
        from desktop.db import db
        try:
            await db.swap_card_containers(card_id_deck, card_id_cand)
        except Exception as exc:
            QMessageBox.critical(self, _("Swap failed"), str(exc))
            return

        # Re-locate the row by identity — indices may have shifted since the
        # confirmation dialog was shown (e.g. a background refresh).
        row = next((i for i, p in enumerate(self._proposals) if p is prop), -1)
        if row >= 0:
            self._proposals.pop(row)
            self._table.removeRow(row)

        # Update status
        total_gain = sum(p["delta"] for p in self._proposals)
        n = len(self._proposals)
        self._status_lbl.setText(
            f"✓ Swap applied.  {n} proposal{'s' if n != 1 else ''} remaining"
            + (f"  ·  Remaining gain: +{total_gain:.1f}" if n else "  ·  All done!")
        )
