"""Deck builder tab widget."""
from __future__ import annotations

import asyncio
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QPlainTextEdit, QMessageBox, QFileDialog,
    QDialog, QDialogButtonBox, QFormLayout,
    QDoubleSpinBox, QCheckBox, QSpinBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from desktop.utils import color_identity_icon
from qasync import asyncSlot


# ── HTML decklist renderer ────────────────────────────────────────────────────

_TYPE_COLORS = {
    "Creatures":     "#2e7d32",
    "Planeswalkers": "#b71c1c",
    "Instants":      "#1565c0",
    "Sorceries":     "#1565c0",
    "Enchantments":  "#6a1b9a",
    "Artifacts":     "#e65100",
    "Lands":         "#546e7a",
    "Other":         "#555555",
}

_HTML_STYLE = """
<style>
body{font-family:'Courier New',Courier,monospace;font-size:10pt;margin:8px;}
.hdr{color:#666;font-size:9pt;border-bottom:1px solid #ddd;padding-bottom:4px;margin-bottom:6px;}
.sec{font-weight:bold;margin-top:10px;margin-bottom:2px;}
.cnt{font-size:8pt;font-weight:normal;color:#999;margin-left:4px;}
.ct{color:#aaa;font-size:8pt;margin-left:6px;}
.cmd-line{color:#f57f17;font-weight:bold;}
.miss{color:#c62828;font-style:italic;}
p{margin:1px 0;}
</style>
"""


def _he(s: str) -> str:
    """HTML-escape a string."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _card_html(n: int, name: str, container: str, extra_class: str = "") -> str:
    cls = f' class="{extra_class}"' if extra_class else ""
    return (
        f'<p{cls}>{n} {_he(name)}'
        f'<span class="ct">&#128230; {_he(container)}</span></p>'
    )


def _section(label: str, count: int, color: str) -> str:
    return (
        f'<p class="sec" style="color:{color};">'
        f'{_he(label)}<span class="cnt">({count})</span></p>'
    )


def _decklist_html(result: dict, fmt: str) -> str:
    from core.deckbuilder._cards import _type_group

    def _dname(card: dict) -> str:
        en  = card.get("name_en") or "?"
        loc = card.get("printed_name") or card.get("name_de") or en
        return f"{loc}  //  {en}" if loc != en else en

    def _cont(card: dict) -> str:
        return card.get("container_name") or "—"

    parts = [f"<html><head>{_HTML_STYLE}</head><body>"]

    if fmt == "commander":
        cmd         = result["commander"]
        arch        = result.get("archetype", "")
        pl          = result.get("power_level", "").title()
        synergy     = result.get("synergy_score", 0)
        parts.append(
            f'<p class="hdr">Commander · {_he(arch)} · {_he(pl)} · Synergy&nbsp;{synergy:.1f}</p>'
        )
        parts.append('<p class="sec" style="color:#f57f17;">Commander</p>')
        parts.append(_card_html(1, _dname(cmd), _cont(cmd), "cmd-line"))

        for group, cards in sorted(result.get("groups", {}).items()):
            color = _TYPE_COLORS.get(group, "#555")
            parts.append(_section(group, len(cards), color))
            for c in cards:
                parts.append(_card_html(1, _dname(c), _cont(c)))

        nonbasic = result.get("nonbasic_lands") or []
        basics   = result.get("basics_from_collection") or []
        missing  = result.get("basics_missing") or {}
        land_cnt = len(nonbasic) + len(basics) + sum(missing.values())
        if land_cnt:
            parts.append(_section("Lands", land_cnt, _TYPE_COLORS["Lands"]))
            for c in nonbasic:
                parts.append(_card_html(1, _dname(c), _cont(c)))
            for c in basics:
                parts.append(_card_html(1, _dname(c), _cont(c)))
            for land, n in sorted(missing.items()):
                parts.append(
                    f'<p class="miss">{n} {_he(land)} — not in collection</p>'
                )
    else:
        fmt_name = result.get("format", "").capitalize()
        strategy = result.get("strategy", "")
        arch     = result.get("archetype", "")
        pl       = result.get("power_level", "").title()
        parts.append(
            f'<p class="hdr">{_he(fmt_name)} · {_he(strategy)} · {_he(arch)} · {_he(pl)}</p>'
        )

        groups: dict[str, list[tuple[dict, int]]] = {}
        for card, n in result.get("deck") or []:
            groups.setdefault(_type_group(card), []).append((card, n))

        for group, group_cards in sorted(groups.items()):
            total = sum(n for _, n in group_cards)
            color = _TYPE_COLORS.get(group, "#555")
            parts.append(_section(group, total, color))
            for c, n in group_cards:
                parts.append(_card_html(n, _dname(c), _cont(c)))

        nonbasic = result.get("nonbasic_lands") or []
        basics   = result.get("basics_from_collection") or []
        missing  = result.get("basics_missing") or {}
        land_cnt = len(nonbasic) + len(basics) + sum(missing.values())
        if land_cnt:
            parts.append(_section("Lands", land_cnt, _TYPE_COLORS["Lands"]))
            for c in nonbasic:
                parts.append(_card_html(1, _dname(c), _cont(c)))
            for c in basics:
                parts.append(_card_html(1, _dname(c), _cont(c)))
            for land, n in sorted(missing.items()):
                parts.append(
                    f'<p class="miss">{n} {_he(land)} — not in collection</p>'
                )

    parts.append("</body></html>")
    return "".join(parts)


FORMATS = [
    ("commander", "Commander (100-card EDH)"),
    ("standard",  "60-card — Standard"),
    ("modern",    "60-card — Modern"),
    ("legacy",    "60-card — Legacy"),
    ("vintage",   "60-card — Vintage"),
    ("pauper",    "60-card — Pauper"),
]


class DeckWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: dict | None = None
        self._last_fmt: str = ""
        self._pool: list[dict] = []
        self._commanders: list[dict] = []
        self._variants: list[dict] = []
        self._plain_text: str = ""
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._load_pool_metadata)

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(QLabel("<h2>Deck Builder</h2>"))

        # Format selector
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._fmt_cb = QComboBox()
        for val, label in FORMATS:
            self._fmt_cb.addItem(label, val)
        fmt_row.addWidget(self._fmt_cb)
        fmt_row.addStretch()
        root.addLayout(fmt_row)

        # ── Commander section ─────────────────────────────────────────────
        self._cmd_section = QWidget()
        cmd_layout = QVBoxLayout(self._cmd_section)
        cmd_layout.setContentsMargins(0, 0, 0, 0)
        cmd_layout.setSpacing(4)

        cmd_filter_row = QHBoxLayout()
        cmd_filter_row.addWidget(QLabel("Commander:"))
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText("Filter commanders…")
        self._cmd_edit.setClearButtonEnabled(True)
        cmd_filter_row.addWidget(self._cmd_edit)
        cmd_layout.addLayout(cmd_filter_row)

        self._cmd_combo = QComboBox()
        self._cmd_combo.addItem("— Auto-pick best commander —", None)
        self._cmd_combo.setMinimumWidth(400)
        cmd_layout.addWidget(self._cmd_combo)

        self._cmd_ci_lbl = QLabel("")
        self._cmd_ci_lbl.setStyleSheet("color: #aaa; font-size: 11px; padding: 2px 0;")
        self._cmd_ci_lbl.setVisible(False)
        cmd_layout.addWidget(self._cmd_ci_lbl)

        root.addWidget(self._cmd_section)

        # ── Strategy section (60-card) ────────────────────────────────────
        self._strategy_section = QWidget()
        strat_layout = QHBoxLayout(self._strategy_section)
        strat_layout.setContentsMargins(0, 0, 0, 0)
        strat_layout.addWidget(QLabel("Strategy:"))
        self._strategy_cb = QComboBox()
        self._strategy_cb.addItem("— Auto-detect —", None)
        self._strategy_cb.setMinimumWidth(200)
        strat_layout.addWidget(self._strategy_cb)
        strat_layout.addStretch()
        root.addWidget(self._strategy_section)

        # ── Build options ─────────────────────────────────────────────────
        opts_row = QHBoxLayout()
        opts_row.addWidget(QLabel("Power level:"))
        self._power_level_cb = QComboBox()
        self._power_level_cb.addItem("Casual",    "casual")
        self._power_level_cb.addItem("Focused",   "focused")
        self._power_level_cb.addItem("Optimized", "optimized")
        self._power_level_cb.setCurrentIndex(1)
        opts_row.addWidget(self._power_level_cb)
        opts_row.addSpacing(16)
        opts_row.addWidget(QLabel("Max card price (€):"))
        self._max_price_sb = QDoubleSpinBox()
        self._max_price_sb.setRange(0.0, 9999.0)
        self._max_price_sb.setSingleStep(0.5)
        self._max_price_sb.setDecimals(2)
        self._max_price_sb.setSpecialValueText("No limit")
        self._max_price_sb.setFixedWidth(90)
        opts_row.addWidget(self._max_price_sb)
        opts_row.addSpacing(16)
        self._refine_cb = QCheckBox("Iterative Refinement")
        self._refine_cb.setToolTip(
            "After building, repeatedly swap low-fit cards for better ones until stable"
        )
        opts_row.addWidget(self._refine_cb)
        opts_row.addSpacing(8)
        opts_row.addWidget(QLabel("Max iterations:"))
        self._refine_iter_sb = QSpinBox()
        self._refine_iter_sb.setRange(1, 10)
        self._refine_iter_sb.setValue(5)
        self._refine_iter_sb.setFixedWidth(50)
        opts_row.addWidget(self._refine_iter_sb)
        opts_row.addStretch()
        root.addLayout(opts_row)

        # ── Build button ──────────────────────────────────────────────────
        build_row = QHBoxLayout()
        self._build_btn = QPushButton("Build deck")
        self._build_btn.setFixedWidth(160)
        build_row.addWidget(self._build_btn)
        build_row.addStretch()
        root.addLayout(build_row)

        # Status / stats
        self._stats_label = QLabel("")
        self._stats_label.setWordWrap(True)
        root.addWidget(self._stats_label)

        # Variant selector (hidden until build returns multiple variants)
        self._variant_widget = QWidget()
        variant_layout = QHBoxLayout(self._variant_widget)
        variant_layout.setContentsMargins(0, 0, 0, 0)
        variant_layout.setSpacing(6)
        variant_layout.addWidget(QLabel("Variant:"))
        self._variant_btns: list[QPushButton] = []
        for i in range(3):
            btn = QPushButton("")
            btn.setCheckable(True)
            btn.setVisible(False)
            btn.clicked.connect(lambda _checked, idx=i: self._on_variant_selected(idx))
            variant_layout.addWidget(btn)
            self._variant_btns.append(btn)
        variant_layout.addStretch()
        self._variant_widget.setVisible(False)
        root.addWidget(self._variant_widget)

        self._curve_label = QLabel("")
        self._curve_label.setFont(_monofont())
        self._curve_label.setStyleSheet("color: #666; font-size: 9px;")
        self._curve_label.setWordWrap(False)
        root.addWidget(self._curve_label)

        # Output — fancy HTML display
        root.addWidget(QLabel("<b>Decklist:</b>"))
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(_monofont())
        root.addWidget(self._output)

        # ── Action buttons ────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy to clipboard")
        self._copy_btn.setToolTip("Copy plain-text decklist to clipboard")
        self._copy_btn.setEnabled(False)
        action_row.addWidget(self._copy_btn)

        self._export_full_btn = QPushButton("Export .txt")
        self._export_full_btn.setToolTip("Save plain-text decklist (with container info)")
        self._export_full_btn.setEnabled(False)
        action_row.addWidget(self._export_full_btn)

        self._export_mtga_btn = QPushButton("Export MTGA/Moxfield")
        self._export_mtga_btn.setToolTip("Save clean format importable into MTGA, Moxfield, etc.")
        self._export_mtga_btn.setEnabled(False)
        action_row.addWidget(self._export_mtga_btn)

        self._manifest_btn = QPushButton("📋 Location Manifest…")
        self._manifest_btn.setToolTip("View, print, or export the card picking manifest sorted by container")
        self._manifest_btn.setEnabled(False)
        action_row.addWidget(self._manifest_btn)

        self._save_btn = QPushButton("📦 Move to deck container…")
        self._save_btn.setToolTip("Move all deck cards from their current containers into a new dedicated container")
        self._save_btn.setEnabled(False)
        action_row.addWidget(self._save_btn)

        action_row.addStretch()
        root.addLayout(action_row)

        # ── Signals ───────────────────────────────────────────────────────
        self._fmt_cb.currentIndexChanged.connect(self._on_format_changed)
        self._cmd_edit.textChanged.connect(self._on_cmd_filter_changed)
        self._cmd_combo.currentIndexChanged.connect(self._on_cmd_selected)
        self._build_btn.clicked.connect(self._on_build)
        self._copy_btn.clicked.connect(self._on_copy)
        self._export_full_btn.clicked.connect(lambda: self._on_export(mtga=False))
        self._export_mtga_btn.clicked.connect(lambda: self._on_export(mtga=True))
        self._manifest_btn.clicked.connect(self._on_manifest)
        self._save_btn.clicked.connect(self._on_save_to_container)
        self._on_format_changed()

    def _on_format_changed(self):
        is_commander = self._fmt_cb.currentData() == "commander"
        self._cmd_section.setVisible(is_commander)
        self._strategy_section.setVisible(not is_commander)

    # ------------------------------------------------------------------ #
    # Pool metadata loading                                                 #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _load_pool_metadata(self):
        from desktop.db import db
        from core.deckbuilder import is_commander_eligible, is_legal, get_available_strategies

        try:
            pool = await db.get_all(exclude_container_types=["deck", "commander"])
        except Exception:
            return

        self._pool = pool

        # Commander list
        self._commanders = sorted(
            [c for c in pool if is_commander_eligible(c) and is_legal(c, "commander")],
            key=lambda c: (c.get("name_en") or "").lower(),
        )
        self._populate_cmd_combo(self._cmd_edit.text())

        # Strategy list
        strategies = get_available_strategies(pool)
        self._strategy_cb.blockSignals(True)
        self._strategy_cb.clear()
        self._strategy_cb.addItem("— Auto-detect —", None)
        for key, display, count in strategies:
            self._strategy_cb.addItem(f"{display}  ({count} cards)", key)
        self._strategy_cb.blockSignals(False)

    def _populate_cmd_combo(self, filter_text: str):
        filt = filter_text.strip().lower()
        prev_id = None
        if self._cmd_combo.currentData() is not None:
            prev_id = (self._cmd_combo.currentData() or {}).get("id")

        self._cmd_combo.blockSignals(True)
        self._cmd_combo.clear()
        self._cmd_combo.addItem("— Auto-pick best commander —", None)
        restore_idx = 0
        for i, cmd in enumerate(self._commanders):
            name = cmd.get("name_en") or ""
            if not filt or filt in name.lower():
                icon = color_identity_icon(cmd.get("color_identity") or [])
                if icon:
                    self._cmd_combo.addItem(icon, name, cmd)
                else:
                    self._cmd_combo.addItem(name, cmd)
                if cmd.get("id") == prev_id:
                    restore_idx = self._cmd_combo.count() - 1
        if restore_idx:
            self._cmd_combo.setCurrentIndex(restore_idx)
        self._cmd_combo.blockSignals(False)

    def _on_cmd_filter_changed(self, text: str):
        self._populate_cmd_combo(text)

    def _on_cmd_selected(self, _index: int):
        import json
        cmd = self._cmd_combo.currentData()
        if cmd is None:
            self._cmd_ci_lbl.setVisible(False)
            return
        ci = cmd.get("color_identity") or []
        if isinstance(ci, str):
            try:
                ci = json.loads(ci)
            except Exception:
                ci = []
        _names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
        _order = "WUBRG"
        sorted_ci = sorted(ci, key=lambda x: _order.index(x) if x in _order else 9)
        if sorted_ci:
            text = "  ·  ".join(_names.get(c, c) for c in sorted_ci)
            self._cmd_ci_lbl.setText(f"Color identity: {text}")
        else:
            self._cmd_ci_lbl.setText("Color identity: Colorless")
        self._cmd_ci_lbl.setVisible(True)

    # ------------------------------------------------------------------ #
    # Build                                                                 #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_build(self):
        from desktop.db import db
        from core.deckbuilder import (
            build_commander_deck, build_60_deck,
            format_commander_decklist, format_60_decklist,
            rank_commanders, iterative_refine,
        )

        fmt = self._fmt_cb.currentData()
        power_level = self._power_level_cb.currentData()
        raw_price = self._max_price_sb.value()
        max_price = raw_price if raw_price > 0.0 else None

        self._build_btn.setEnabled(False)
        self._stats_label.setText("Loading collection…")
        self._output.clear()
        self._plain_text = ""
        self._copy_btn.setEnabled(False)
        self._export_full_btn.setEnabled(False)
        self._export_mtga_btn.setEnabled(False)
        self._manifest_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._curve_label.setText("")
        self._variant_widget.setVisible(False)

        try:
            pool = await db.get_all(exclude_container_types=["deck", "commander"])
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to load collection:\n{exc}")
            self._build_btn.setEnabled(True)
            self._stats_label.setText("")
            return

        self._pool = pool

        try:
            deck_affinity, _affinity_n_decks = await db.get_deck_card_affinity(
                fmt if fmt != "commander" else "commander"
            )
        except Exception:
            deck_affinity, _affinity_n_decks = {}, 0

        if fmt == "commander":
            combo_card = self._cmd_combo.currentData()
            if combo_card is not None:
                cmd_id = combo_card.get("id")
                commander = next((c for c in pool if c.get("id") == cmd_id), None)
                if commander is None:
                    cmd_name = combo_card.get("name_en", "")
                    commander = next(
                        (c for c in pool if (c.get("name_en") or "").lower() == cmd_name.lower()),
                        None,
                    )
                if commander is None:
                    QMessageBox.warning(
                        self, "Not in collection",
                        f"'{combo_card.get('name_en', '?')}' was not found in your collection.\n"
                        "Only cards you own can be used in the deck."
                    )
                    self._build_btn.setEnabled(True)
                    self._stats_label.setText("")
                    return
            else:
                ranked = rank_commanders(pool)
                if not ranked:
                    QMessageBox.warning(
                        self, "No commanders",
                        "No commander-eligible legendary creatures found in the collection."
                    )
                    self._build_btn.setEnabled(True)
                    self._stats_label.setText("")
                    return
                commander, score = ranked[0]
                self._stats_label.setText(
                    f"Auto-selected: {commander.get('name_en', '')} (synergy score: {score})"
                )

            result = build_commander_deck(
                commander, pool, power_level=power_level, max_price=max_price,
                deck_affinity=deck_affinity,
            )
        else:
            forced = self._strategy_cb.currentData()
            result = build_60_deck(
                pool, fmt, forced_strategy=forced,
                power_level=power_level, max_price=max_price,
                deck_affinity=deck_affinity,
            )

        # ── Iterative refinement ────────────────────────────────────────
        if self._refine_cb.isChecked():
            self._stats_label.setText(
                self._render_stats(result, fmt) + "  |  Refining…"
            )
            max_iter = self._refine_iter_sb.value()
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None, lambda: iterative_refine(result, pool, max_iterations=max_iter)
                )
            except Exception as exc:
                self._stats_label.setText(f"Refinement error: {exc}")

            # Refine all variants too
            refined_variants: list[dict] = []
            for v in result.get("variants") or [result]:
                if v is result:
                    refined_variants.append(result)
                else:
                    try:
                        rv = await loop.run_in_executor(
                            None,
                            lambda _v=v: iterative_refine(_v, pool, max_iterations=max_iter),
                        )
                        refined_variants.append(rv)
                    except Exception:
                        refined_variants.append(v)
            if len(refined_variants) > 1:
                result["variants"] = refined_variants

        plain = (
            format_commander_decklist(result)
            if fmt == "commander"
            else format_60_decklist(result)
        )

        result["_affinity_deck_count"] = _affinity_n_decks

        self._result = result
        self._last_fmt = fmt
        self._plain_text = plain
        self._output.setHtml(_decklist_html(result, fmt))
        self._curve_label.setText(self._compact_curve(result.get("curve") or {}))
        self._stats_label.setText(self._render_stats(result, fmt))
        self._update_variant_selector(result)
        self._copy_btn.setEnabled(True)
        self._export_full_btn.setEnabled(True)
        self._export_mtga_btn.setEnabled(True)
        self._manifest_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._build_btn.setEnabled(True)

    def _render_stats(self, result: dict, fmt: str) -> str:
        parts: list[str] = []
        if fmt == "commander":
            cmd = result.get("commander") or {}
            parts.append(f"Commander: {cmd.get('name_en', '?')}")
        else:
            strategy = result.get("strategy", "")
            if strategy:
                parts.append(f"Strategy: {strategy}")

        arch = result.get("archetype", "")
        conf = result.get("archetype_confidence", 0)
        if arch:
            arch_str = f"{arch} ({conf:.0%})" if conf else arch
            parts.append(f"Archetype: {arch_str}")

        pl = result.get("power_level", "")
        if pl:
            parts.append(pl.title())

        role = result.get("role_summary") or {}
        if role:
            role_str = "  ·  ".join(
                f"{k.title()}: {v}"
                for k, v in role.items()
                if v
            )
            if role_str:
                parts.append(role_str)

        col_count = result.get("collection_count", 0)
        padding   = result.get("padding_basics", 0)
        missing   = result.get("basics_missing") or {}
        base_str  = f"{col_count} from collection"
        if padding:
            base_str += f"  (⚑ +{padding} basic{'s' if padding != 1 else ''} added as filler)"
        if missing:
            missing_str = ", ".join(f"{n}× {land}" for land, n in sorted(missing.items()))
            parts.append(f"{base_str}  ⚠ Basics missing: {missing_str}")
        else:
            parts.append(base_str)

        val = result.get("value_eur", 0)
        parts.append(f"€{val:.2f}")

        synergy = result.get("synergy_score", 0)
        if synergy:
            parts.append(f"Synergy: {synergy:.1f}")

        try:
            from core.analysis import rate_deck
            _all_cards = (
                [c for c, _ in (result.get("deck") or [])]
                + (result.get("nonbasic_lands") or [])
                + (result.get("basics_from_collection") or [])
            )
            _fmt_key = "commander" if fmt == "commander" else "60"
            _rating = rate_deck(
                _all_cards, _fmt_key,
                result.get("archetype", ""),
                synergy=synergy,
                archetype_conf=result.get("archetype_confidence"),
            )
            parts.append(f"Grade: {_rating['grade']}  ({_rating['overall']:.0f}/100)")
        except Exception:
            pass

        n_decks = result.get("_affinity_deck_count", 0)
        if n_decks:
            parts.append(f"Learned from {n_decks} deck{'s' if n_decks != 1 else ''}")

        swaps = result.get("refinement_swaps")
        if swaps is not None:
            if swaps > 0:
                iters = result.get("refinement_iterations", 0)
                parts.append(f"Refined: {swaps} swap{'s' if swaps != 1 else ''} in {iters} iter{'s' if iters != 1 else ''}")
            else:
                parts.append("Already optimal")

        return "  |  ".join(parts)

    def _update_variant_selector(self, result: dict):
        variants = result.get("variants") or []
        if len(variants) > 1:
            self._variants = variants
            for i, btn in enumerate(self._variant_btns):
                if i < len(variants):
                    v = variants[i]
                    arch = v.get("archetype", f"Variant {i + 1}")
                    conf = v.get("archetype_confidence", 0)
                    label = f"{arch} ({conf:.0%})" if conf else arch
                    btn.setText(label)
                    btn.setChecked(i == 0)
                    btn.setVisible(True)
                else:
                    btn.setVisible(False)
            self._variant_widget.setVisible(True)
        else:
            self._variants = []
            for btn in self._variant_btns:
                btn.setVisible(False)
            self._variant_widget.setVisible(False)

    def _on_variant_selected(self, idx: int):
        if idx >= len(self._variants):
            return
        from core.deckbuilder import format_commander_decklist, format_60_decklist

        for i, btn in enumerate(self._variant_btns):
            if btn.isVisible():
                btn.setChecked(i == idx)

        variant = self._variants[idx]
        self._result = variant
        fmt = self._last_fmt
        self._plain_text = (
            format_commander_decklist(variant)
            if fmt == "commander"
            else format_60_decklist(variant)
        )
        self._output.setHtml(_decklist_html(variant, fmt))
        self._curve_label.setText(self._compact_curve(variant.get("curve") or {}))
        self._stats_label.setText(self._render_stats(variant, fmt))

    @staticmethod
    def _compact_curve(curve: dict) -> str:
        if not curve:
            return ""
        max_count = max(curve.values(), default=1)
        BAR = 8
        labels = {0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6+"}
        parts = []
        for b in range(7):
            n = curve.get(b, 0)
            if n == 0:
                continue
            bar = "█" * max(1, round(n / max_count * BAR))
            parts.append(f"{labels[b]}:{bar}{n}")
        return "  ".join(parts)

    def _on_copy(self):
        if self._plain_text:
            QGuiApplication.clipboard().setText(self._plain_text)

    def _on_export(self, mtga: bool):
        from datetime import date
        from core.deckbuilder import (
            format_commander_decklist, format_commander_decklist_mtga,
            format_60_decklist, format_60_decklist_mtga,
        )

        result = self._result
        fmt = self._last_fmt
        if not result:
            return

        if fmt == "commander":
            cmd_name = (result["commander"].get("name_en") or "deck").replace(" ", "_")
            suffix = "_mtga" if mtga else "_full"
            default_name = f"{cmd_name}{suffix}_{date.today()}.txt"
            text = format_commander_decklist_mtga(result) if mtga else format_commander_decklist(result)
        else:
            suffix = "_mtga" if mtga else "_full"
            default_name = f"{fmt}{suffix}_{date.today()}.txt"
            text = format_60_decklist_mtga(result) if mtga else format_60_decklist(result)

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Decklist", default_name,
            "Text files (*.txt);;All files (*)",
        )
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

    def _on_manifest(self):
        from core.deckbuilder import format_location_manifest
        result = self._result
        fmt = self._last_fmt
        if not result:
            return
        manifest_text = format_location_manifest(result, fmt)
        if not manifest_text:
            QMessageBox.information(self, "Manifest", "No collection cards with container data.")
            return
        dlg = _ManifestDialog(manifest_text, parent=self)
        dlg.exec()

    # ------------------------------------------------------------------ #
    # Save to container                                                     #
    # ------------------------------------------------------------------ #

    def _on_save_to_container(self):
        if not self._result:
            return
        card_ids = self._collect_card_ids()
        dlg = _NewDeckContainerDialog(self._last_fmt, self._result, len(card_ids), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, ctype, deck_format = dlg.values()
            asyncio.ensure_future(self._do_save_to_container(name, ctype, deck_format))

    async def _do_save_to_container(self, name: str, ctype: str, deck_format: str | None):
        from desktop.db import db

        card_ids = self._collect_card_ids()
        if not card_ids:
            QMessageBox.warning(self, "No cards", "No collection cards to move.")
            return

        try:
            container_id = await db.create_container(name, type=ctype)
            await db.set_container_deck_format(container_id, deck_format)
            await db.move_cards_to_container(card_ids, container_id)

            # Auto-mark commander and persist color identity
            if deck_format == "commander" and self._last_fmt == "commander":
                cmd = self._result.get("commander", {})
                if cmd.get("id") and cmd["id"] in card_ids:
                    await db.set_commander(cmd["id"], True, container_id)
                # Store color identity on the container
                import json as _json
                from core.deckbuilder import color_identity as _ci
                ci = list(_ci(cmd))
                if ci:
                    await db.set_container_color_identity(container_id, _json.dumps(ci))

            QMessageBox.information(
                self, "Saved",
                f"Moved {len(card_ids)} card(s) to container '{name}'.\n\n"
                "Use 'Location Manifest…' to see where each card originally was."
            )
            # Refresh pool metadata so commanders / strategies are up to date
            await self._load_pool_metadata()
        except Exception as exc:
            QMessageBox.critical(self, "Error", str(exc))

    def _collect_card_ids(self) -> list[int]:
        result = self._result
        fmt = self._last_fmt
        if not result:
            return []

        ids: list[int] = []

        if fmt == "commander":
            cmd = result.get("commander", {})
            if cmd.get("id"):
                ids.append(cmd["id"])
            ids += [c["id"] for c in result.get("deck", []) if c.get("id")]
            ids += [c["id"] for c in result.get("nonbasic_lands", []) if c.get("id")]
            ids += [c["id"] for c in result.get("basics_from_collection", []) if c.get("id")]
        else:
            name_to_ids: dict[str, list[int]] = {}
            for c in self._pool:
                name = (c.get("name_en") or "").lower()
                cid = c.get("id")
                if cid:
                    name_to_ids.setdefault(name, []).append(cid)

            for card, count in result.get("deck", []):
                name = (card.get("name_en") or "").lower()
                available = name_to_ids.get(name, [])
                ids += available[:count]
            ids += [c["id"] for c in result.get("nonbasic_lands", []) if c.get("id")]
            ids += [c["id"] for c in result.get("basics_from_collection", []) if c.get("id")]

        # Deduplicate while preserving order
        seen: set[int] = set()
        return [i for i in ids if not (i in seen or seen.add(i))]


# ── Location manifest dialog ──────────────────────────────────────────────────

class _ManifestDialog(QDialog):
    """Shows the location manifest with print and export options."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Location Manifest")
        self.setMinimumSize(600, 500)
        self._text = text

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setFont(_monofont())
        self._view.setPlainText(text)
        layout.addWidget(self._view)

        btn_row = QHBoxLayout()

        print_btn = QPushButton("🖨 Print…")
        print_btn.clicked.connect(self._on_print)
        btn_row.addWidget(print_btn)

        save_btn = QPushButton("💾 Save as .txt…")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._on_copy)
        btn_row.addWidget(copy_btn)

        btn_row.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _on_print(self):
        try:
            from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
        except ImportError:
            QMessageBox.warning(self, "Unavailable", "Printing support is not installed.")
            return
        printer = QPrinter()
        dlg = QPrintDialog(printer, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._view.document().print_(printer)

    def _on_save(self):
        from datetime import date
        default_name = f"manifest_{date.today()}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Manifest", default_name, "Text files (*.txt);;All files (*)"
        )
        if path:
            try:
                Path(path).write_text(self._text, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "Save failed", str(exc))

    def _on_copy(self):
        QGuiApplication.clipboard().setText(self._text)


# ── New deck container dialog ─────────────────────────────────────────────────

class _NewDeckContainerDialog(QDialog):
    def __init__(self, deck_format: str, result: dict, card_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move deck to container")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # Summary label
        if deck_format == "commander":
            cmd_name = (result.get("commander") or {}).get("name_en") or ""
            summary = f"Commander: {cmd_name}  ·  {card_count} cards will be moved"
        else:
            strategy = result.get("strategy", "")
            summary = f"Strategy: {strategy}  ·  {card_count} cards will be moved"
        lbl = QLabel(summary)
        lbl.setStyleSheet("color: #888; font-size: 11px;")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        info = QLabel(
            "Cards are moved from their current containers into the new one.\n"
            "Use 'Location Manifest…' before moving to record where each card originally was."
        )
        info.setStyleSheet("color: #666; font-size: 10px;")
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. Atraxa Commander Deck")
        # Auto-populate name
        if deck_format == "commander":
            cmd_name = (result.get("commander") or {}).get("name_en") or ""
            if cmd_name:
                self._name_edit.setText(f"{cmd_name} EDH")
        else:
            strategy = result.get("strategy", "")
            if strategy:
                self._name_edit.setText(f"{strategy.replace('tribal_', '').title()} {deck_format.title()}")
        form.addRow("Name:", self._name_edit)

        import core.config as cfg
        types = cfg.load().get("container_types", ["binder", "deck", "box"])
        self._type_cb = QComboBox()
        self._type_cb.addItems(types)
        preferred = "commander" if deck_format == "commander" else "deck"
        if preferred in types:
            self._type_cb.setCurrentText(preferred)
        elif "deck" in types:
            self._type_cb.setCurrentText("deck")
        form.addRow("Type:", self._type_cb)

        self._format_cb = QComboBox()
        self._format_cb.addItem("— no format —", None)
        self._format_cb.addItem("⚔ Commander", "commander")
        self._format_cb.addItem("60-card Standard", "standard")
        self._format_cb.addItem("60-card Timeless", "timeless")
        idx = self._format_cb.findData(deck_format)
        if idx >= 0:
            self._format_cb.setCurrentIndex(idx)
        form.addRow("Format:", self._format_cb)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Move cards")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_accept(self):
        if not self._name_edit.text().strip():
            QMessageBox.warning(self, "Name required", "Please enter a name for the container.")
            return
        self.accept()

    def values(self) -> tuple[str, str, str | None]:
        return (
            self._name_edit.text().strip(),
            self._type_cb.currentText(),
            self._format_cb.currentData(),
        )


def _monofont():
    from PyQt6.QtGui import QFont
    font = QFont("Monospace")
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPointSize(9)
    return font
