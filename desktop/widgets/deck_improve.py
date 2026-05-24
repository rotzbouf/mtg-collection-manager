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
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from qasync import asyncSlot

from desktop.widgets.card_detail import CardDetailPanel
from desktop.utils import display_name

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


# ── Widget ─────────────────────────────────────────────────────────────────────

_COL_REMOVE  = 0
_COL_TYPE_R  = 1
_COL_SCORE_R = 2
_COL_ARROW   = 3
_COL_ADD     = 4
_COL_LOC     = 5
_COL_SCORE_A = 6
_COL_DELTA   = 7
_COL_ACCEPT  = 8
_N_COLS      = 9

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

        header.addWidget(QLabel("Deck:"))
        self._deck_combo = QComboBox()
        self._deck_combo.setMinimumWidth(200)
        self._deck_combo.currentIndexChanged.connect(self._on_deck_changed)
        header.addWidget(self._deck_combo)

        self._format_lbl = QLabel("")
        self._format_lbl.setStyleSheet("color: #888; font-size: 12px; margin-left: 8px;")
        header.addWidget(self._format_lbl)

        header.addStretch()

        self._filter_colors_chk = QCheckBox("Color identity filter")
        self._filter_colors_chk.setChecked(True)
        self._filter_colors_chk.setToolTip(
            "Only suggest cards whose color identity fits the deck"
        )
        self._filter_colors_chk.stateChanged.connect(self._on_filter_changed)
        header.addWidget(self._filter_colors_chk)

        self._refresh_btn = QPushButton("↻  Refresh")
        self._refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(self._refresh_btn)

        root.addLayout(header)

        # ── Status label ──────────────────────────────────────────────── #
        self._status_lbl = QLabel("Select a deck to see improvement proposals.")
        self._status_lbl.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(self._status_lbl)

        # ── Main split: table (left) + card detail (right) ────────────── #
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Proposals table
        self._table = QTableWidget(0, _N_COLS)
        self._table.setHorizontalHeaderLabels([
            "Remove from deck", "Type", "Score",
            "→",
            "Swap in", "Location", "Score", "Δ Score",
            "",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_REMOVE,  QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_TYPE_R,  QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_SCORE_R, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ARROW,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ADD,     QHeaderView.ResizeMode.Stretch)
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

        # Card detail panel (shows the "swap in" candidate)
        right_frame = QFrame()
        right_frame.setFrameShape(QFrame.Shape.NoFrame)
        right_layout = QVBoxLayout(right_frame)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(4)

        detail_lbl = QLabel("Candidate card")
        detail_lbl.setStyleSheet("font-size: 11px; color: #888;")
        right_layout.addWidget(detail_lbl)

        self._detail = CardDetailPanel(show_buttons=False)
        right_layout.addWidget(self._detail)

        splitter.addWidget(right_frame)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter, stretch=1)

        # ── Legend ────────────────────────────────────────────────────── #
        legend = QHBoxLayout()
        for color, text in [(_TIER1_BG, "■ Strong (score 0 → ≥5)"),
                             (_TIER2_BG, "■ Moderate (≥2× better)")]:
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
                f"⚠  Unknown format '{deck_format}' — cannot map to meta data."
            )
            self._table.setRowCount(0)
            self._proposals = []
            return

        self._format_lbl.setText(_META_LABELS.get(meta_fmt, meta_fmt))
        self._status_lbl.setText("Loading…")
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
            self._status_lbl.setText("No (non-land) cards found in this deck.")
            self._table.setRowCount(0)
            self._proposals = []
            return

        if not candidates:
            self._status_lbl.setText(
                f"No binder/box cards have meta scores for {_META_LABELS.get(meta_fmt, meta_fmt)}. "
                "Try running a meta crawl first."
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
                "No improvements found — your binder has no better-scoring cards of the same types."
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

            # Remove col
            remove_item = _item(display_name(dc))
            remove_item.setFont(bold)
            self._table.setItem(row, _COL_REMOVE,  remove_item)
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
            self._table.setItem(row, _COL_LOC,     _item(f"📦 {loc}", center))
            self._table.setItem(row, _COL_SCORE_A, _item(f"{cand.get('meta_score', 0):.1f}", center))
            self._table.setItem(row, _COL_DELTA,   _item(f"+{prop['delta']:.1f}", center))

            # Accept button (embedded as a push-button via a widget)
            accept_btn = QPushButton("Accept")
            accept_btn.setStyleSheet(
                "QPushButton { font-size: 11px; padding: 2px 8px; }"
                "QPushButton:hover { background: #2e7d32; }"
            )
            accept_btn.clicked.connect(lambda checked, r=row: self._on_accept(r))
            self._table.setCellWidget(row, _COL_ACCEPT, accept_btn)

        self._table.blockSignals(False)
        self._detail.clear()

    # ------------------------------------------------------------------ #
    # Actions                                                               #
    # ------------------------------------------------------------------ #

    def _on_selection_changed(self):
        row = self._table.currentRow()
        if 0 <= row < len(self._proposals):
            cand = self._proposals[row]["candidate"]
            self._detail.set_card(cand)
        else:
            self._detail.clear()

    def _on_accept(self, row: int):
        if row >= len(self._proposals):
            return
        prop = self._proposals[row]
        dc   = prop["deck_card"]
        cand = prop["candidate"]

        deck_name  = display_name(dc)
        cand_name  = display_name(cand)
        cand_loc   = cand.get("container_name") or "unassigned"
        deck_loc   = dc.get("container_name") or "deck"

        reply = QMessageBox.question(
            self, "Confirm swap",
            f"Swap cards physically?\n\n"
            f"  Remove:  {deck_name}\n"
            f"    from:  {deck_loc}\n\n"
            f"  Add:     {cand_name}\n"
            f"    from:  {cand_loc}\n\n"
            f"The two cards will exchange containers.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        _bg(self._do_swap(row, dc["id"], cand["id"]))

    @asyncSlot()
    async def _do_swap(self, row: int, card_id_deck: int, card_id_cand: int):
        from desktop.db import db
        try:
            await db.swap_card_containers(card_id_deck, card_id_cand)
        except Exception as exc:
            QMessageBox.critical(self, "Swap failed", str(exc))
            return

        # Remove this row from the table and proposal list
        self._proposals.pop(row)
        self._table.removeRow(row)

        # Update status
        total_gain = sum(p["delta"] for p in self._proposals)
        n = len(self._proposals)
        self._status_lbl.setText(
            f"✓ Swap applied.  {n} proposal{'s' if n != 1 else ''} remaining"
            + (f"  ·  Remaining gain: +{total_gain:.1f}" if n else "  ·  All done!")
        )
