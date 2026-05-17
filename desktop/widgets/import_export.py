"""Import / Export / Backup tab widget."""
from __future__ import annotations

import asyncio

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame,
    QProgressBar,
)
from PyQt6.QtCore import Qt
from qasync import asyncSlot


class ImportExportWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._import_rows: list[dict] = []
        self._import_format: str = ""
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(16)

        # ---- Export section ----
        layout.addWidget(self._header("Export Collection"))
        export_row = QHBoxLayout()
        self._exp_csv_btn = QPushButton("Export CSV")
        self._exp_json_btn = QPushButton("Export JSON")
        self._exp_mox_btn = QPushButton("Export Moxfield CSV")
        export_row.addWidget(self._exp_csv_btn)
        export_row.addWidget(self._exp_json_btn)
        export_row.addWidget(self._exp_mox_btn)
        export_row.addStretch()
        layout.addLayout(export_row)
        self._exp_status = QLabel("")
        layout.addWidget(self._exp_status)

        layout.addWidget(self._separator())

        # ---- Import section ----
        layout.addWidget(self._header("Import Cards"))
        import_row = QHBoxLayout()
        self._imp_btn = QPushButton("Choose file…")
        self._imp_btn.setToolTip("Select a .csv or .json file to import")
        import_row.addWidget(self._imp_btn)
        import_row.addStretch()
        layout.addLayout(import_row)

        self._imp_status = QLabel("No file selected.")
        layout.addWidget(self._imp_status)

        # Preview table (first 10 rows)
        self._preview_table = QTableWidget(0, 5)
        self._preview_table.setHorizontalHeaderLabels(
            ["Name", "Set", "Language", "Condition", "Foil"]
        )
        self._preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview_table.setMaximumHeight(220)
        self._preview_table.setVisible(False)
        layout.addWidget(self._preview_table)

        self._imp_confirm_btn = QPushButton("Confirm import")
        self._imp_confirm_btn.setEnabled(False)
        self._imp_progress = QProgressBar()
        self._imp_progress.setVisible(False)
        layout.addWidget(self._imp_confirm_btn)
        layout.addWidget(self._imp_progress)

        layout.addWidget(self._separator())

        # ---- Backup section ----
        layout.addWidget(self._header("Backup & Restore"))
        backup_row = QHBoxLayout()
        self._backup_btn = QPushButton("Download backup…")
        self._restore_btn = QPushButton("Restore backup…")
        backup_row.addWidget(self._backup_btn)
        backup_row.addWidget(self._restore_btn)
        backup_row.addStretch()
        layout.addLayout(backup_row)
        self._backup_status = QLabel("")
        layout.addWidget(self._backup_status)

        layout.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

        # Signals
        self._exp_csv_btn.clicked.connect(self._on_export_csv)
        self._exp_json_btn.clicked.connect(self._on_export_json)
        self._exp_mox_btn.clicked.connect(self._on_export_moxfield)
        self._imp_btn.clicked.connect(self._on_choose_import)
        self._imp_confirm_btn.clicked.connect(self._on_confirm_import)
        self._backup_btn.clicked.connect(self._on_backup)
        self._restore_btn.clicked.connect(self._on_restore)

    @staticmethod
    def _header(text: str) -> QLabel:
        lbl = QLabel(f"<h3>{text}</h3>")
        return lbl

    @staticmethod
    def _separator() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        return sep

    # ------------------------------------------------------------------ #
    # Export                                                                #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_export_csv(self):
        await self._export("csv")

    @asyncSlot()
    async def _on_export_json(self):
        await self._export("json")

    @asyncSlot()
    async def _on_export_moxfield(self):
        await self._export("moxfield")

    async def _export(self, fmt: str):
        from desktop.db import db
        from core.exporter import to_csv, to_json, to_moxfield

        filters = {
            "csv":      ("CSV Files (*.csv)", ".csv"),
            "json":     ("JSON Files (*.json)", ".json"),
            "moxfield": ("CSV Files (*.csv)", ".csv"),
        }
        filt, ext = filters[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Save file", f"collection{ext}", filt)
        if not path:
            return

        self._exp_status.setText("Exporting…")
        try:
            cards = await db.get_all()
            if fmt == "csv":
                content = await asyncio.to_thread(to_csv, cards)
            elif fmt == "json":
                content = await asyncio.to_thread(to_json, cards)
            else:
                content = await asyncio.to_thread(to_moxfield, cards)

            await asyncio.to_thread(
                lambda: open(path, "w", encoding="utf-8").write(content)
            )
            self._exp_status.setText(f"Exported {len(cards)} cards to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export error", str(exc))
            self._exp_status.setText("Export failed.")

    # ------------------------------------------------------------------ #
    # Import                                                                #
    # ------------------------------------------------------------------ #

    def _on_choose_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose import file",
            "", "Supported files (*.csv *.json)"
        )
        if not path:
            return
        self._do_parse_import(path)

    @asyncSlot()
    async def _do_parse_import(self, path: str):
        from core.importer import detect_format, parse_moxfield_csv, parse_full_csv, parse_json

        try:
            raw = await asyncio.to_thread(lambda: open(path, "rb").read())
            fmt = await asyncio.to_thread(detect_format, path, raw)
            if fmt == "moxfield_csv":
                rows = await asyncio.to_thread(parse_moxfield_csv, raw)
            elif fmt == "full_csv":
                rows = await asyncio.to_thread(parse_full_csv, raw)
            else:
                rows = await asyncio.to_thread(parse_json, raw)
        except Exception as exc:
            QMessageBox.critical(self, "Parse error", str(exc))
            self._imp_status.setText("Failed to parse file.")
            self._imp_confirm_btn.setEnabled(False)
            return

        self._import_rows = rows
        self._import_format = fmt
        total = len(rows)
        self._imp_status.setText(
            f"Format: {fmt}  |  {total} card(s) found. Preview of first 10:"
        )

        # Populate preview table
        preview = rows[:10]
        self._preview_table.setRowCount(len(preview))
        for row_idx, r in enumerate(preview):
            name = r.get("name") or r.get("name_en") or ""
            set_code = r.get("set_code") or ""
            lang = r.get("language") or ""
            cond = r.get("condition") or ""
            foil = str(r.get("foil") or "")
            for col, val in enumerate([name, set_code, lang, cond, foil]):
                item = QTableWidgetItem(str(val))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._preview_table.setItem(row_idx, col, item)

        self._preview_table.setVisible(True)
        self._imp_confirm_btn.setEnabled(total > 0)

    @asyncSlot()
    async def _on_confirm_import(self):
        from desktop.db import db, scryfall
        from core.importer import normalize_row

        rows = self._import_rows
        fmt = self._import_format
        if not rows:
            return

        self._imp_confirm_btn.setEnabled(False)
        self._imp_progress.setVisible(True)
        self._imp_progress.setMaximum(len(rows))
        self._imp_progress.setValue(0)

        containers_cache: dict[str, int] = {}
        imported = 0
        errors = 0

        if fmt == "moxfield_csv":
            # Need Scryfall lookup for each card
            for i, row in enumerate(rows):
                self._imp_progress.setValue(i + 1)
                try:
                    name = row.get("name") or ""
                    set_code = row.get("set_code") or None
                    card, _lang = await scryfall.resolve_card(name, set_code=set_code)
                    if card is None:
                        errors += 1
                        continue
                    card.update({
                        "condition": row.get("condition", "NM"),
                        "language":  row.get("language", "en"),
                        "foil":      1 if row.get("foil") else 0,
                        "quantity":  1,
                    })
                    await db.add_card(card, added_by="desktop-import")
                    imported += 1
                except Exception:
                    errors += 1
        else:
            # Full CSV or JSON — each row is already a complete card dict
            containers_list = await db.list_containers()
            for c in containers_list:
                containers_cache[c["name"]] = c["id"]

            for i, row in enumerate(rows):
                self._imp_progress.setValue(i + 1)
                try:
                    card, container_name = await asyncio.to_thread(normalize_row, row)
                    if container_name and container_name not in containers_cache:
                        cid = await db.create_container(container_name)
                        containers_cache[container_name] = cid
                    if container_name:
                        card["container_id"] = containers_cache.get(container_name)
                    await db.add_card(card, added_by="desktop-import")
                    imported += 1
                except Exception:
                    errors += 1

        self._imp_progress.setVisible(False)
        self._imp_confirm_btn.setEnabled(False)
        self._imp_status.setText(
            f"Import complete: {imported} imported, {errors} errors."
        )
        if errors:
            QMessageBox.warning(self, "Import", f"Imported {imported} cards; {errors} failed.")

    # ------------------------------------------------------------------ #
    # Backup                                                                #
    # ------------------------------------------------------------------ #

    @asyncSlot()
    async def _on_backup(self):
        from desktop.db import db

        path, _ = QFileDialog.getSaveFileName(
            self, "Save backup", "mtg_backup.db", "Database (*.db)"
        )
        if not path:
            return

        self._backup_status.setText("Creating backup…")
        try:
            data = await db.backup_bytes()
            await asyncio.to_thread(lambda: open(path, "wb").write(data))
            self._backup_status.setText(f"Backup saved to {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Backup error", str(exc))
            self._backup_status.setText("Backup failed.")

    @asyncSlot()
    async def _on_restore(self):
        from desktop.db import db

        path, _ = QFileDialog.getOpenFileName(
            self, "Open backup", "", "Database (*.db)"
        )
        if not path:
            return

        try:
            data = await asyncio.to_thread(lambda: open(path, "rb").read())
            info = await db.inspect_backup(data)
        except Exception as exc:
            QMessageBox.critical(self, "Invalid backup", str(exc))
            return

        reply = QMessageBox.question(
            self, "Restore backup",
            f"Restore backup?\n"
            f"It contains {info['cards']} cards and {info['containers']} containers.\n"
            f"The current database will be REPLACED.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._backup_status.setText("Restoring…")
        try:
            await db.restore_from_bytes(data)
            self._backup_status.setText("Restore complete.")
            QMessageBox.information(self, "Restore", "Database restored successfully.")
        except Exception as exc:
            QMessageBox.critical(self, "Restore error", str(exc))
            self._backup_status.setText("Restore failed.")
