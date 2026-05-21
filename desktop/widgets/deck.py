"""Deck builder tab widget."""
from __future__ import annotations

import asyncio
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QMessageBox, QFileDialog,
    QDialog, QDialogButtonBox, QFormLayout,
    QDoubleSpinBox,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from desktop.utils import color_identity_icon
from qasync import asyncSlot


FORMATS = [
    ("commander", "Commander (100-card EDH)"),
    ("timeless",  "60-card — Timeless"),
    ("standard",  "60-card — Standard"),
]


class DeckWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: dict | None = None
        self._last_fmt: str = ""
        self._pool: list[dict] = []
        self._commanders: list[dict] = []
        self._variants: list[dict] = []
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

        # Output
        root.addWidget(QLabel("<b>Decklist:</b>"))
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(_monofont())
        root.addWidget(self._output)

        # ── Action buttons ────────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._copy_btn = QPushButton("Copy to clipboard")
        self._copy_btn.setEnabled(False)
        action_row.addWidget(self._copy_btn)

        self._export_full_btn = QPushButton("Export .txt")
        self._export_full_btn.setToolTip("Save decklist with container locations (picking reference)")
        self._export_full_btn.setEnabled(False)
        action_row.addWidget(self._export_full_btn)

        self._export_mtga_btn = QPushButton("Export MTGA/Moxfield")
        self._export_mtga_btn.setToolTip("Save clean format importable into MTGA, Moxfield, etc.")
        self._export_mtga_btn.setEnabled(False)
        action_row.addWidget(self._export_mtga_btn)

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
            rank_commanders,
        )

        fmt = self._fmt_cb.currentData()
        power_level = self._power_level_cb.currentData()
        raw_price = self._max_price_sb.value()
        max_price = raw_price if raw_price > 0.0 else None

        self._build_btn.setEnabled(False)
        self._stats_label.setText("Loading collection…")
        self._output.setPlainText("")
        self._copy_btn.setEnabled(False)
        self._export_full_btn.setEnabled(False)
        self._export_mtga_btn.setEnabled(False)
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
                commander, pool, power_level=power_level, max_price=max_price
            )
            text = format_commander_decklist(result)
        else:
            forced = self._strategy_cb.currentData()
            result = build_60_deck(
                pool, fmt, forced_strategy=forced,
                power_level=power_level, max_price=max_price,
            )
            text = format_60_decklist(result)

        self._result = result
        self._last_fmt = fmt
        self._output.setPlainText(text)
        self._curve_label.setText(self._compact_curve(result.get("curve") or {}))
        self._stats_label.setText(self._render_stats(result, fmt))
        self._update_variant_selector(result)
        self._copy_btn.setEnabled(True)
        self._export_full_btn.setEnabled(True)
        self._export_mtga_btn.setEnabled(True)
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
        missing = result.get("basics_missing") or {}
        if missing:
            missing_str = ", ".join(f"{n}× {land}" for land, n in sorted(missing.items()))
            parts.append(f"{col_count} from collection  ⚠ Basics missing: {missing_str}")
        else:
            parts.append(f"{col_count} from collection")

        val = result.get("value_eur", 0)
        parts.append(f"€{val:.2f}")

        synergy = result.get("synergy_score", 0)
        if synergy:
            parts.append(f"Synergy: {synergy:.1f}")

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
        if fmt == "commander":
            text = format_commander_decklist(variant)
        else:
            text = format_60_decklist(variant)
        self._output.setPlainText(text)
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
        text = self._output.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

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
                "The location manifest in the decklist shows where each card "
                "was originally stored."
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
            "The decklist's location manifest shows where each card originally was."
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
