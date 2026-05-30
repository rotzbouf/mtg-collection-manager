"""Deck completion assistant.

Analyses an existing (possibly incomplete) deck container, builds a theme
profile from the oracle text of its cards, then scores every card in the
collection that fits the deck's color identity and is not already in a deck.
The result is a ranked list of candidates the player can move into the deck
with one click.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qasync import asyncSlot

from desktop.utils import display_name, format_price, lang_flag, RARITY_COLORS
from desktop.widgets.card_detail import CardDetailPanel
from core.i18n import _

_log = logging.getLogger(__name__)

_CARD_ROLE  = Qt.ItemDataRole.UserRole
_SCORE_ROLE = Qt.ItemDataRole.UserRole + 1   # raw float score for sorting

_COLS = [_("Name"), _("CMC"), _("Type"), _("Synergy"), _("Matched themes"), _("Container"), _("Price (EUR)")]


def _bg(coro):
    task = asyncio.ensure_future(coro)
    task.add_done_callback(
        lambda f: _log.error("DeckComplete background error: %s", f.exception())
        if not f.cancelled() and f.exception() else None
    )
    return task


# ── Color identity ─────────────────────────────────────────────────────────────

_COLOR_NAMES = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
_COLOR_SYMBOLS = {"W": "⬜", "U": "🔵", "B": "⚫", "R": "🔴", "G": "🟢"}


def _parse_ci(card: dict) -> set[str]:
    raw = card.get("color_identity") or "[]"
    try:
        ci = json.loads(raw) if isinstance(raw, str) else list(raw)
        return {c.upper() for c in ci if isinstance(c, str)}
    except Exception:
        return set()


def _fits_ci(card: dict, deck_colors: set[str]) -> bool:
    """True when the card's color identity is a subset of the deck's colors."""
    ci = _parse_ci(card)
    return not ci or ci <= deck_colors


# ── Theme engine ───────────────────────────────────────────────────────────────

# Each entry: (compiled regex, human-readable label)
# Patterns are intentionally broad to catch as many relevant cards as possible.
_THEMES: list[tuple[re.Pattern, str]] = [
    *[(re.compile(p, re.I), lbl) for p, lbl in [
        # ── Card advantage ──
        (r'draw (?:a|an|two|three|\d+) cards?',              'card draw'),
        (r'\bscry \d+',                                       'scry'),
        (r'\bsurveil \d+',                                    'surveil'),
        (r'look at the top \d+ cards?',                       'top-deck manipulation'),
        (r'exile .{0,40}you may (?:cast|play) it',           'impulse draw'),

        # ── Tokens ──
        (r'creates? .{0,60}token',                            'token creation'),
        (r'token creatures? you control',                     'tokens matter'),
        (r'for each (?:creature |)token',                     'tokens matter'),
        (r'number of tokens',                                 'tokens matter'),

        # ── +1/+1 counters ──
        (r'\+1/\+1 counter',                                  '+1/+1 counters'),
        (r'\bproliferate\b',                                  'proliferate'),
        (r'for each counter',                                 'counters matter'),
        (r'remove .{0,20}counter',                            'counter removal'),

        # ── Sacrifice / death ──
        (r'\bsacrifice\b',                                    'sacrifice'),
        (r'\bdies?\b',                                        'death trigger'),
        (r'whenever .{0,50}creature dies?',                   'death trigger'),
        (r'when .{0,30}you sacrifice',                        'sacrifice'),

        # ── Graveyard / recursion ──
        (r'\bgraveyard\b',                                    'graveyard'),
        (r'\bmill\b',                                         'mill'),
        (r'return .{0,60}from (?:your |a )?graveyard',        'recursion'),
        (r'from your graveyard (?:to|onto)',                  'recursion'),
        (r'\breadied?\b',                                     'recursion'),
        (r'flashback\b',                                      'flashback'),
        (r'\bunearth\b|\boutpost siege\b|\bescapeb',          'recursion'),

        # ── ETB ──
        (r'enters? (?:the )?battlefield',                     'ETB'),
        (r'whenever .{0,50}enters? (?:the )?battlefield',     'ETB trigger'),
        (r'flicker|blink .{0,30}exile',                       'blink/flicker'),

        # ── Combat ──
        (r'whenever .{0,50}\battacks?\b',                     'attack trigger'),
        (r'whenever .{0,50}deals? (?:combat )?damage',        'damage trigger'),
        (r'\bmenace\b',                                       'menace'),
        (r'\bfirst strike\b|\bdouble strike\b',               'first/double strike'),
        (r'\btrample\b',                                      'trample'),
        (r'\bhaste\b',                                        'haste'),
        (r'\bdeathtouch\b',                                   'deathtouch'),
        (r'\bvigilance\b',                                    'vigilance'),
        (r'\blifelink\b',                                     'lifegain'),

        # ── Spells matter ──
        (r'whenever you cast',                                'spells matter'),
        (r'instant or sorcery',                               'instants/sorceries'),
        (r'noncreature spell',                                'spells matter'),
        (r'\bmagecraft\b',                                    'spells matter'),
        (r'whenever a spell',                                 'spells matter'),

        # ── Artifacts ──
        (r'\bartifact\b',                                     'artifacts'),
        (r'\bequip\b',                                        'equipment'),
        (r'\bcrew\b',                                         'vehicles'),

        # ── Enchantments ──
        (r'\benchantment\b',                                  'enchantments'),
        (r'\baura\b',                                         'auras'),
        (r'\bconstellation\b',                                'enchantments'),

        # ── Ramp / mana ──
        (r'add \{[WUBRGC2]',                                  'mana ramp'),
        (r'search your library for .{0,40}(?:basic )?land',   'land ramp'),
        (r'land card from .{0,30}library',                    'land ramp'),
        (r'put .{0,20}land .{0,20}onto the battlefield',      'land ramp'),

        # ── Life ──
        (r'you gain \d+ life',                                'lifegain'),
        (r'you gain that much life',                          'lifegain'),
        (r'whenever you gain life',                           'lifegain matters'),
        (r'your life total',                                  'life matters'),

        # ── Flying / evasion ──
        (r'\bflying\b',                                       'flying'),
        (r'\breach\b',                                        'reach'),
        (r'\bindestructible\b',                               'indestructible'),
        (r'\bhexproof\b|\bshroud\b',                          'protection'),
        (r'\bprotection from\b',                              'protection'),

        # ── Removal ──
        (r'destroy target (?!player)',                        'removal'),
        (r'exile target (?!player)',                          'removal'),
        (r'\bcounter target spell\b',                         'counterspell'),
        (r'counter target (?:activated|triggered)',           'counterspell'),

        # ── Bounce ──
        (r"return .{0,60}to its owner.{0,10}hand",            'bounce'),
        (r"return .{0,60}to your hand",                       'bounce'),

        # ── Burn ──
        (r'deals? \d+ damage to (?:any|target|each)',         'burn'),
        (r'deals? damage equal to',                           'burn'),

        # ── Discard ──
        (r'\bdiscard\b',                                      'discard'),
        (r'each opponent discards',                           'discard'),

        # ── Pump / anthem ──
        (r'gets? \+\d+/\+\d+',                               'pump'),
        (r'(?:other |each ).{0,40}creatures? .{0,20}gets? \+','anthem'),

        # ── Tribal ──
        (r'\b(?:elf|elves)\b',                                'elf tribal'),
        (r'\bgoblin\b',                                       'goblin tribal'),
        (r'\bzombie\b',                                       'zombie tribal'),
        (r'\bmerfolk\b',                                      'merfolk tribal'),
        (r'\bsoldier\b',                                      'soldier tribal'),
        (r'\bknight\b',                                       'knight tribal'),
        (r'\bwizard\b',                                       'wizard tribal'),
        (r'\bwarrior\b',                                      'warrior tribal'),
        (r'\bangel\b',                                        'angel tribal'),
        (r'\bdragon\b',                                       'dragon tribal'),
        (r'\bvampire\b',                                      'vampire tribal'),
        (r'\bhuman\b',                                        'human tribal'),
        (r'\bspirit\b',                                       'spirit tribal'),
        (r'\bcleric\b',                                       'cleric tribal'),
        (r'\brogue\b',                                        'rogue tribal'),
        (r'\bpirate\b',                                       'pirate tribal'),
        (r'\bdinosaur\b',                                     'dinosaur tribal'),
        (r'\bcat\b',                                          'cat tribal'),
        (r'\bbird\b',                                         'bird tribal'),
    ]]
]


def _themes_of(oracle_text: str) -> set[str]:
    """Return the set of theme labels matching this oracle text."""
    text = oracle_text or ""
    return {lbl for pat, lbl in _THEMES if pat.search(text)}


def _build_deck_profile(deck_cards: list[dict]) -> Counter:
    """Count how many deck cards carry each theme."""
    profile: Counter = Counter()
    for card in deck_cards:
        for t in _themes_of(card.get("oracle_text") or ""):
            profile[t] += 1
    return profile


def _score_candidate(
    card: dict,
    deck_profile: Counter,
    n_deck: int,
) -> tuple[float, list[str]]:
    """Return (synergy_score, matched_themes).

    Score is the sum of per-theme weights (= deck_profile[t] / n_deck).
    A card that reinforces themes that half the deck already cares about
    scores much higher than one that touches a theme only one card has.
    """
    if not deck_profile or not n_deck:
        return 0.0, []
    themes = _themes_of(card.get("oracle_text") or "")
    matched = [t for t in themes if t in deck_profile]
    if not matched:
        return 0.0, []
    score = sum(deck_profile[t] / n_deck for t in matched)
    return round(score, 4), sorted(matched)


def _synergy_stars(score: float) -> str:
    """Convert a raw score to a ★ display string (1–5 stars)."""
    if score <= 0:
        return ""
    if score < 0.10:
        return "★"
    if score < 0.25:
        return "★★"
    if score < 0.45:
        return "★★★"
    if score < 0.70:
        return "★★★★"
    return "★★★★★"


def _rarity_color(rarity: str) -> QColor:
    return QColor(RARITY_COLORS.get((rarity or "").lower(), "#888888"))


# ── Main widget ────────────────────────────────────────────────────────────────

class DeckCompleteWidget(QWidget):
    """Suggest cards from the collection that would complete an unfinished deck."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._decks:            list[dict] = []
        self._results:          list[dict] = []   # scored candidate dicts
        self._last_deck_colors: set[str]   = set()
        self._loading = False
        self._build_ui()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def db_ready(self):
        QTimer.singleShot(0, lambda: _bg(self._load_decks()))

    def refresh(self):
        _bg(self._load_decks())

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Deck selector group ──────────────────────────────────────── #
        top_box = QGroupBox(_("Deck to complete"))
        top_layout = QVBoxLayout(top_box)
        top_layout.setSpacing(6)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(_("Deck:")))
        self._deck_combo = QComboBox()
        self._deck_combo.setMinimumWidth(260)
        row1.addWidget(self._deck_combo, stretch=1)
        row1.addSpacing(16)
        row1.addWidget(QLabel(_("Target size:")))
        self._target_sb = QSpinBox()
        self._target_sb.setRange(1, 250)
        self._target_sb.setValue(100)
        self._target_sb.setFixedWidth(72)
        self._target_sb.setToolTip(
            _("Total number of cards the deck should have.\n"
              "60 for Standard/Modern, 100 for Commander.")
        )
        row1.addWidget(self._target_sb)
        self._analyze_btn = QPushButton("🔍  " + _("Analyze"))
        self._analyze_btn.setStyleSheet(
            "QPushButton { background-color: #1e2a4a; border: 1px solid #4a6a9a; padding: 5px 14px; }"
            "QPushButton:hover { background-color: #2a3a6a; }"
        )
        row1.addWidget(self._analyze_btn)
        top_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self._deck_status = QLabel(_("Select a deck and click Analyze."))
        self._deck_status.setStyleSheet("color: #a6adc8; font-size: 12px;")
        row2.addWidget(self._deck_status, stretch=1)
        top_layout.addLayout(row2)

        root.addWidget(top_box)

        # ── Filter bar ──────────────────────────────────────────────── #
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(_("Min synergy:")))
        self._min_score_sb = QDoubleSpinBox()
        self._min_score_sb.setRange(0.0, 2.0)
        self._min_score_sb.setSingleStep(0.05)
        self._min_score_sb.setValue(0.05)
        self._min_score_sb.setDecimals(2)
        self._min_score_sb.setFixedWidth(72)
        self._min_score_sb.setToolTip(
            _("Hide candidates whose synergy score is below this threshold.\n"
              "0 = show everything; 0.10 = at least one strong shared theme.")
        )
        filter_row.addWidget(self._min_score_sb)
        filter_row.addSpacing(16)
        self._color_filter_chk = QCheckBox(_("Color identity filter"))
        self._color_filter_chk.setChecked(True)
        self._color_filter_chk.setToolTip(
            _("When checked, only suggest cards whose color identity is within\n"
              "the deck's color identity (no off-color splashes).")
        )
        filter_row.addWidget(self._color_filter_chk)
        filter_row.addSpacing(16)
        self._basic_filter_chk = QCheckBox(_("Hide basic lands"))
        self._basic_filter_chk.setChecked(True)
        filter_row.addWidget(self._basic_filter_chk)
        filter_row.addStretch()
        self._result_count_lbl = QLabel("")
        self._result_count_lbl.setStyleSheet("color: #a6adc8; font-size: 12px;")
        filter_row.addWidget(self._result_count_lbl)
        root.addLayout(filter_row)

        # ── Splitter: table + detail panel ──────────────────────────── #
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._table = QTableWidget()
        self._table.setColumnCount(len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSortingEnabled(True)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)           # Name
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # CMC
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Type
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Synergy
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)           # Themes
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)  # Container
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # Price
        self._table.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        splitter.addWidget(self._table)

        self._detail = CardDetailPanel(show_buttons=False)
        self._detail.setMinimumWidth(260)
        self._detail.setMaximumWidth(360)
        splitter.addWidget(self._detail)
        splitter.setSizes([720, 300])
        root.addWidget(splitter)

        # ── Bottom action bar ────────────────────────────────────────── #
        bottom = QHBoxLayout()
        self._add_btn = QPushButton("➕  " + _("Add selected to deck"))
        self._add_btn.setEnabled(False)
        self._add_btn.setStyleSheet(
            "QPushButton { background-color: #1e3a1e; border: 1px solid #4a8a4a; padding: 5px 14px; }"
            "QPushButton:hover { background-color: #2a5a2a; }"
            "QPushButton:disabled { color: #7f849c; border-color: #45475a; }"
        )
        bottom.addWidget(self._add_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        # ── Signals ─────────────────────────────────────────────────── #
        self._deck_combo.currentIndexChanged.connect(self._on_deck_changed)
        self._analyze_btn.clicked.connect(lambda: _bg(self._analyze()))
        self._min_score_sb.valueChanged.connect(lambda _: self._apply_filter())
        self._color_filter_chk.stateChanged.connect(lambda _: self._apply_filter())
        self._basic_filter_chk.stateChanged.connect(lambda _: self._apply_filter())
        self._table.currentItemChanged.connect(self._on_selection_changed)
        self._table.itemSelectionChanged.connect(self._on_multiselect_changed)
        self._add_btn.clicked.connect(lambda: _bg(self._add_selected_to_deck()))

    # ------------------------------------------------------------------ #
    # Data loading                                                          #
    # ------------------------------------------------------------------ #

    async def _load_decks(self):
        from desktop.db import db
        try:
            containers = await db.list_containers()
        except Exception as exc:
            _log.error("DeckComplete: failed to load containers: %s", exc)
            return

        self._decks = [
            c for c in containers
            if c.get("type") in ("deck", "commander")
        ]

        prev_id = self._deck_combo.currentData() if self._deck_combo.count() else None

        self._deck_combo.blockSignals(True)
        self._deck_combo.clear()
        for ct in self._decks:
            fmt  = ct.get("deck_format") or ct.get("type") or "?"
            cnt  = ct.get("card_count", "?")
            self._deck_combo.addItem(
                f"{ct['name']}  ({fmt} · {cnt} cards)",
                userData=ct["id"],
            )
        if prev_id is not None:
            for i in range(self._deck_combo.count()):
                if self._deck_combo.itemData(i) == prev_id:
                    self._deck_combo.setCurrentIndex(i)
                    break
        self._deck_combo.blockSignals(False)

        # Auto-set target size from deck type
        self._on_deck_changed(self._deck_combo.currentIndex())

    def _on_deck_changed(self, _idx: int):
        container_id = self._deck_combo.currentData()
        if container_id is None:
            return
        deck_info = next((d for d in self._decks if d["id"] == container_id), None)
        if not deck_info:
            return
        dtype = deck_info.get("type") or ""
        if dtype == "commander":
            self._target_sb.setValue(100)
        elif dtype == "deck":
            fmt = (deck_info.get("deck_format") or "").lower()
            if fmt in ("commander", "brawl", "oathbreaker"):
                self._target_sb.setValue(100)
            else:
                self._target_sb.setValue(60)
        card_count = deck_info.get("card_count", 0) or 0
        target     = self._target_sb.value()
        missing    = max(0, target - card_count)
        self._deck_status.setText(
            _("{count} cards currently  ·  {missing} slot(s) to fill  ·  "
              "click Analyze to get suggestions").format(
                count=card_count, missing=missing)
        )
        self._results = []
        self._table.setRowCount(0)
        self._add_btn.setEnabled(False)
        self._result_count_lbl.setText("")

    # ------------------------------------------------------------------ #
    # Analysis                                                              #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _analyze(self):
        if self._loading:
            return
        container_id = self._deck_combo.currentData()
        if container_id is None:
            return
        deck_info = next((d for d in self._decks if d["id"] == container_id), None)
        if not deck_info:
            return

        self._loading = True
        self._analyze_btn.setEnabled(False)
        self._deck_status.setText(_("Loading deck cards…"))
        self._table.setRowCount(0)
        self._results = []
        self._add_btn.setEnabled(False)
        self._detail.clear()

        try:
            from desktop.db import db
            # ── Fetch deck cards ──────────────────────────────────────
            deck_cards = await db.list_cards(
                container_id=container_id, limit=300, sort="chaos"
            )

            if not deck_cards:
                self._deck_status.setText(_("⚠  Deck is empty — add some cards first."))
                return

            # ── Determine deck color identity ─────────────────────────
            deck_colors: set[str] = set()
            for card in deck_cards:
                deck_colors |= _parse_ci(card)

            # ── Build theme profile ───────────────────────────────────
            self._deck_status.setText(_("Building synergy profile…"))
            deck_profile = await asyncio.to_thread(_build_deck_profile, deck_cards)
            deck_names   = {(c.get("name_en") or "").strip().lower() for c in deck_cards}

            if not deck_profile:
                self._deck_status.setText(
                    _("⚠  Could not extract themes — oracle text may be missing.\n"
                      "Try 'Fix missing data' in Maintenance → Scryfall Data Sync first.")
                )
                return

            # ── Fetch collection candidates ───────────────────────────
            self._deck_status.setText(_("Fetching collection candidates…"))
            raw_candidates = await db.get_collection_candidates(container_id)

            # ── Score candidates ──────────────────────────────────────
            self._deck_status.setText(_("Scoring {n:,} candidate card(s)…").format(n=len(raw_candidates)))

            def _score_all(cards, profile, n):
                scored = []
                for card in cards:
                    name_lo = (card.get("name_en") or "").strip().lower()
                    if name_lo in deck_names:
                        continue   # already in the deck
                    score, matched = _score_candidate(card, profile, n)
                    scored.append({**card, "_score": score, "_themes": matched})
                # Sort: score desc, then name asc
                scored.sort(key=lambda c: (-c["_score"], c.get("name_en") or ""))
                return scored

            self._results          = await asyncio.to_thread(
                _score_all, raw_candidates, deck_profile, len(deck_cards)
            )
            self._last_deck_colors = deck_colors

            # ── Status bar ────────────────────────────────────────────
            target      = self._target_sb.value()
            missing     = max(0, target - len(deck_cards))
            color_str   = " ".join(_COLOR_SYMBOLS.get(c, c) for c in sorted(deck_colors)) or "Colorless"
            top_themes  = [t for t, _ in deck_profile.most_common(5)]
            self._deck_status.setText(
                f"{len(deck_cards)} / {target} cards  ·  {missing} slot(s) to fill  ·  "
                f"Colors: {color_str}  ·  "
                f"Top themes: {', '.join(top_themes)}"
            )

            self._apply_filter()

        except Exception as exc:
            _log.exception("DeckComplete analysis error")
            self._deck_status.setText(_("Error: {exc}").format(exc=exc))
        finally:
            self._loading = False
            self._analyze_btn.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Filtering & rendering                                                 #
    # ------------------------------------------------------------------ #

    def _apply_filter(self):
        """Re-render the table using current filter settings."""
        if not self._results:
            return

        min_score    = self._min_score_sb.value()
        filter_color = self._color_filter_chk.isChecked()
        hide_basics  = self._basic_filter_chk.isChecked()
        deck_colors  = self._last_deck_colors

        visible: list[dict] = []
        for card in self._results:
            score = card.get("_score", 0.0)
            if score < min_score:
                continue
            if filter_color and deck_colors and not _fits_ci(card, deck_colors):
                continue
            if hide_basics and "Basic Land" in (card.get("type_line") or ""):
                continue
            visible.append(card)

        self._populate_table(visible)
        synergetic = sum(1 for c in visible if c.get("_score", 0) > 0)
        self._result_count_lbl.setText(
            f"{len(visible):,} candidate(s)  ·  {synergetic:,} with synergy"
        )

    def _populate_table(self, cards: list[dict]):
        tbl = self._table
        tbl.setSortingEnabled(False)
        tbl.setRowCount(len(cards))

        for row, card in enumerate(cards):
            score   = card.get("_score", 0.0)
            themes  = card.get("_themes", [])
            rarity  = (card.get("rarity") or "").lower()
            type_ln = (card.get("type_line") or "").split("—")[0].strip()
            cmc_val = card.get("cmc") or 0

            def _item(
                text: str,
                align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
                sort_key=None,
            ) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                if sort_key is not None:
                    it.setData(Qt.ItemDataRole.UserRole, sort_key)
                return it

            # 0 — Name
            name_item = _item(display_name(card))
            name_item.setData(_CARD_ROLE, card)
            name_item.setToolTip(
                (card.get("oracle_text") or "").replace("\n", "  ·  ")[:300]
            )
            tbl.setItem(row, 0, name_item)

            # 1 — CMC (sort numerically)
            cmc_item = _item(
                str(int(cmc_val)) if cmc_val else "—",
                Qt.AlignmentFlag.AlignRight,
                sort_key=float(cmc_val),
            )
            tbl.setItem(row, 1, cmc_item)

            # 2 — Type
            tbl.setItem(row, 2, _item(type_ln))

            # 3 — Synergy stars (sort by raw score)
            stars      = _synergy_stars(score)
            score_item = _item(
                f"{stars}  {score:.0%}" if score > 0 else "—",
                Qt.AlignmentFlag.AlignCenter,
                sort_key=score,
            )
            if score >= 0.45:
                score_item.setForeground(QColor("#f0c060"))
            elif score >= 0.25:
                score_item.setForeground(QColor("#7eb8f7"))
            elif score > 0:
                score_item.setForeground(QColor("#888888"))
            score_item.setData(_SCORE_ROLE, score)
            tbl.setItem(row, 3, score_item)

            # 4 — Matched themes
            theme_str = ", ".join(themes) if themes else "—"
            theme_item = _item(theme_str)
            theme_item.setToolTip("\n".join(themes) if themes else "No shared themes found")
            tbl.setItem(row, 4, theme_item)

            # 5 — Container
            tbl.setItem(row, 5, _item(card.get("container_name") or "—"))

            # 6 — Price (sort numerically)
            price = card.get("price_eur")
            price_item = _item(
                format_price(price, card.get("price_approx", 0)),
                Qt.AlignmentFlag.AlignRight,
                sort_key=float(price or 0),
            )
            tbl.setItem(row, 6, price_item)

        tbl.setSortingEnabled(True)
        tbl.sortByColumn(3, Qt.SortOrder.DescendingOrder)

    # ------------------------------------------------------------------ #
    # Interactions                                                          #
    # ------------------------------------------------------------------ #

    def _on_selection_changed(self, current: QTableWidgetItem, _prev):
        if current is None:
            self._detail.clear()
            return
        item = self._table.item(current.row(), 0)
        if item:
            card = item.data(_CARD_ROLE)
            if card:
                self._detail.set_card(card)

    def _on_multiselect_changed(self):
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        self._add_btn.setEnabled(bool(rows))

    def _selected_cards(self) -> list[dict]:
        seen: set[int] = set()
        cards: list[dict] = []
        for idx in self._table.selectedIndexes():
            if idx.column() != 0:
                continue
            item = self._table.item(idx.row(), 0)
            if not item:
                continue
            card = item.data(_CARD_ROLE)
            cid  = card.get("id") if card else None
            if cid and cid not in seen:
                seen.add(cid)
                cards.append(card)
        return cards

    async def _add_selected_to_deck(self):
        cards = self._selected_cards()
        if not cards:
            return
        container_id = self._deck_combo.currentData()
        if container_id is None:
            return

        from desktop.db import db
        card_ids = [c["id"] for c in cards if c.get("id")]
        try:
            moved = await db.move_cards_to_container(card_ids, container_id)
            QMessageBox.information(
                self,
                _("Cards added"),
                _("✓  Added {moved} card(s) to the deck.").format(moved=moved),
            )
            # Re-run analysis so the counts and suggestions refresh
            await self._load_decks()
            await self._analyze()
        except Exception as exc:
            QMessageBox.critical(self, _("Error"), str(exc))
