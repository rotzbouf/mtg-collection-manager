"""Settings widget — Import / Export, Backup / Restore, config editor, and bot control."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QFrame,
    QProgressBar, QTabWidget, QLineEdit, QGroupBox,
    QFormLayout, QScrollArea, QCheckBox, QListWidget,
    QListWidgetItem, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QProcess, QProcessEnvironment
from qasync import asyncSlot

import core.config as cfg

_PROJECT_ROOT  = Path(__file__).parent.parent.parent
_VENV_PYTHON   = _PROJECT_ROOT / "venv" / "bin" / "python"
_BOT_SCRIPT    = _PROJECT_ROOT / "server" / "bot.py"
_MAX_LOG_LINES = 500


def _is_bundled() -> bool:
    import sys as _sys
    return getattr(_sys, 'frozen', False)


def _bot_launch() -> tuple[str, list[str], str]:
    """Return (program, args, cwd) for launching the Discord bot subprocess."""
    if _is_bundled():
        cwd = str(Path(sys.executable).parent)
        return sys.executable, ['--run-bot'], cwd
    python = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
    return python, [str(_BOT_SCRIPT)], str(_PROJECT_ROOT)


# (group_title, [(dotted_config_key, field_label, tooltip, is_secret)])
# dotted_config_key format: "section.key"  e.g. "discord.token"
_CONFIG_GROUPS: list[tuple[str, list[tuple[str, str, str, bool]]]] = [
    ("Discord", [
        ("discord.token",               "Bot Token",
         "Your Discord bot token from the Developer Portal. Required to run the bot.",
         True),
        ("discord.guild_id",            "Guild ID",
         "Restrict slash command sync to a single guild for faster updates during development. Leave blank for global sync.",
         False),
        ("discord.scan_channel_id",     "Scan Channel ID",
         "Channel where card images are auto-scanned and all collection commands work.",
         False),
        ("discord.showcase_channel_id", "Showcase Channel ID",
         "Channel where the welcome showcase is shown when someone writes. Leave blank to allow any channel.",
         False),
    ]),
    ("Roles", [
        ("discord.guest_role",      "Guest Role",
         "Role name or ID required for read-only commands. Leave blank = everyone.",
         False),
        ("discord.collector_role",  "Collector Role",
         "Role required to add / scan cards and create containers.",
         False),
        ("discord.admin_role",      "Admin Role",
         "Role required to remove cards, delete / rename containers, and run admin commands.",
         False),
    ]),
    ("App", [
        ("app.debug_scan_preview", "Debug Scan Preview",
         "Set to 1 to send a debug preview image after each scan. Keep at 0 in production.",
         False),
    ]),
]


class SettingsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._import_rows: list[dict] = []
        self._import_format: str = ""
        self._sync_cancelled = False
        self._bot_process: QProcess | None = None
        self._build_ui()

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        root.addWidget(QLabel("<h2>Settings</h2>"))

        tabs = QTabWidget()
        tabs.addTab(self._build_services_tab(), "Services")
        tabs.addTab(self._build_maintenance_tab(), "Maintenance")
        tabs.addTab(self._build_env_tab(), "Configuration")
        tabs.addTab(self._build_containers_tab(), "Containers & Overcount")
        tabs.addTab(self._build_buylists_tab(), "Buylists")
        root.addWidget(tabs)

        # Signals
        self._exp_csv_btn.clicked.connect(self._on_export_csv)
        self._exp_json_btn.clicked.connect(self._on_export_json)
        self._exp_mox_btn.clicked.connect(self._on_export_moxfield)
        self._imp_btn.clicked.connect(self._on_choose_import)
        self._imp_confirm_btn.clicked.connect(self._on_confirm_import)
        self._backup_btn.clicked.connect(self._on_backup)
        self._restore_btn.clicked.connect(self._on_restore)

    def _build_services_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Discord Bot ──────────────────────────────────────────────── #
        bot_box = QGroupBox("Discord Bot")
        bot_layout = QVBoxLayout(bot_box)
        bot_layout.setSpacing(8)

        bot_status_row = QHBoxLayout()
        bot_status_row.addWidget(QLabel("Status:"))
        self._bot_status_lbl = QLabel("○ Stopped")
        self._bot_status_lbl.setStyleSheet("font-weight: bold; color: #888;")
        bot_status_row.addWidget(self._bot_status_lbl)
        bot_status_row.addStretch()
        bot_layout.addLayout(bot_status_row)

        _py, _args, _ = _bot_launch()
        info = QLabel(f"<small>{_py}  ·  {' '.join(_args)}</small>")
        info.setStyleSheet("color: #555;")
        bot_layout.addWidget(info)

        bot_btn_row = QHBoxLayout()
        self._bot_start_btn = QPushButton("▶  Start bot")
        self._bot_stop_btn  = QPushButton("■  Stop bot")
        self._bot_stop_btn.setEnabled(False)
        bot_btn_row.addWidget(self._bot_start_btn)
        bot_btn_row.addWidget(self._bot_stop_btn)
        bot_btn_row.addStretch()
        bot_layout.addLayout(bot_btn_row)

        self._bot_log = QPlainTextEdit()
        self._bot_log.setReadOnly(True)
        self._bot_log.setMaximumBlockCount(_MAX_LOG_LINES)
        self._bot_log.setMaximumHeight(160)
        self._bot_log.setStyleSheet(
            "background: #0d0d1a; color: #cccccc; font-family: monospace; font-size: 11px;"
        )
        bot_layout.addWidget(self._bot_log)

        bot_log_ctrl = QHBoxLayout()
        self._bot_clear_btn = QPushButton("Clear log")
        self._bot_autoscroll_cb = QCheckBox("Auto-scroll")
        self._bot_autoscroll_cb.setChecked(True)
        bot_log_ctrl.addWidget(self._bot_clear_btn)
        bot_log_ctrl.addWidget(self._bot_autoscroll_cb)
        bot_log_ctrl.addStretch()
        bot_layout.addLayout(bot_log_ctrl)

        self._bot_start_btn.clicked.connect(self._on_bot_start)
        self._bot_stop_btn.clicked.connect(self._on_bot_stop)
        self._bot_clear_btn.clicked.connect(self._bot_log.clear)

        layout.addWidget(bot_box)
        layout.addStretch()

        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    # ------------------------------------------------------------------ #
    # Bot process control                                                   #
    # ------------------------------------------------------------------ #

    def _on_bot_start(self):
        if self._bot_process and self._bot_process.state() != QProcess.ProcessState.NotRunning:
            return

        python, args, cwd = _bot_launch()

        self._bot_process = QProcess(self)
        self._bot_process.setProgram(python)
        self._bot_process.setArguments(args)
        self._bot_process.setWorkingDirectory(cwd)

        # Inherit current environment so .env variables are picked up
        env = QProcessEnvironment.systemEnvironment()
        self._bot_process.setProcessEnvironment(env)

        self._bot_process.readyReadStandardOutput.connect(self._on_bot_stdout)
        self._bot_process.readyReadStandardError.connect(self._on_bot_stderr)
        self._bot_process.stateChanged.connect(self._on_bot_state_changed)
        self._bot_process.finished.connect(self._on_bot_finished)

        self._bot_process.start()

        from core.desktop_bridge import bridge
        bridge.start()

    def _on_bot_stop(self):
        if self._bot_process is None:
            return
        state = self._bot_process.state()
        if state == QProcess.ProcessState.Running:
            self._bot_process.terminate()
        elif state == QProcess.ProcessState.Starting:
            self._bot_process.kill()
        from core.desktop_bridge import bridge
        bridge.stop()

    def _on_bot_stdout(self):
        if self._bot_process is None:
            return
        raw = bytes(self._bot_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append_log(raw.rstrip())

    def _on_bot_stderr(self):
        if self._bot_process is None:
            return
        raw = bytes(self._bot_process.readAllStandardError()).decode("utf-8", errors="replace")
        self._append_log(raw.rstrip(), error=True)

    def _append_log(self, text: str, error: bool = False):
        if not text:
            return
        for line in text.splitlines():
            self._bot_log.appendPlainText(f"[err] {line}" if error else line)
        if self._bot_autoscroll_cb.isChecked():
            self._bot_log.verticalScrollBar().setValue(
                self._bot_log.verticalScrollBar().maximum()
            )

    def _on_bot_state_changed(self, state: QProcess.ProcessState):
        if state == QProcess.ProcessState.NotRunning:
            self._bot_status_lbl.setText("○ Stopped")
            self._bot_status_lbl.setStyleSheet("font-weight: bold; color: #888;")
            self._bot_start_btn.setEnabled(True)
            self._bot_stop_btn.setEnabled(False)
        elif state == QProcess.ProcessState.Starting:
            self._bot_status_lbl.setText("⏳ Starting…")
            self._bot_status_lbl.setStyleSheet("font-weight: bold; color: #d4af37;")
            self._bot_start_btn.setEnabled(False)
            self._bot_stop_btn.setEnabled(False)
        elif state == QProcess.ProcessState.Running:
            self._bot_status_lbl.setText("● Running")
            self._bot_status_lbl.setStyleSheet("font-weight: bold; color: #7ec8a0;")
            self._bot_start_btn.setEnabled(False)
            self._bot_stop_btn.setEnabled(True)

    def _on_bot_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        if exit_status == QProcess.ExitStatus.CrashExit:
            self._append_log("[bot] Process crashed.")
        else:
            self._append_log(f"[bot] Exited with code {exit_code}.")

    def bot_stop_for_close(self) -> bool:
        """Called by MainWindow.closeEvent. Returns True if the bot was running."""
        if self._bot_process and self._bot_process.state() != QProcess.ProcessState.NotRunning:
            self._bot_process.terminate()
            return True
        return False

    def _build_maintenance_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        # ── Scryfall Data Sync ──────────────────────────────────────────── #
        layout.addWidget(self._section_header("Scryfall Data Sync"))
        layout.addWidget(QLabel(
            "Re-fetch card data from Scryfall to keep oracle text, prices and images up to date.\n"
            "Sync operations run in the background and can be cancelled at any time."
        ))

        btn_grid = QHBoxLayout()
        self._sync_missing_btn = QPushButton("↻ Load missing data")
        self._sync_missing_btn.setToolTip(
            "Re-fetch only cards that are missing oracle text, type line or price (fast)"
        )
        self._sync_full_btn = QPushButton("↻ Full reload")
        self._sync_full_btn.setToolTip(
            "Re-fetch all card data from Scryfall — slow on large collections"
        )
        self._sync_lang_btn = QPushButton("🌐 Fix language data")
        self._sync_lang_btn.setToolTip(
            "Check non-English cards for missing localized text/name and back-fill from Scryfall"
        )
        self._sync_dfc_btn = QPushButton("🔄 Fix DFC data")
        self._sync_dfc_btn.setToolTip(
            "Re-fetch double-faced cards that are missing mana cost or back-face image"
        )
        btn_grid.addWidget(self._sync_missing_btn)
        btn_grid.addWidget(self._sync_full_btn)
        btn_grid.addWidget(self._sync_lang_btn)
        btn_grid.addWidget(self._sync_dfc_btn)
        btn_grid.addStretch()
        layout.addLayout(btn_grid)

        self._sync_progress = QProgressBar()
        self._sync_progress.setTextVisible(False)
        self._sync_progress.setFixedHeight(8)
        self._sync_progress.setVisible(False)
        layout.addWidget(self._sync_progress)

        sync_status_row = QHBoxLayout()
        self._sync_status = QLabel("")
        self._sync_status.setStyleSheet("color: #888; font-size: 12px;")
        self._sync_cancel_btn = QPushButton("Cancel")
        self._sync_cancel_btn.setFixedWidth(70)
        self._sync_cancel_btn.setVisible(False)
        sync_status_row.addWidget(self._sync_status, stretch=1)
        sync_status_row.addWidget(self._sync_cancel_btn)
        layout.addLayout(sync_status_row)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Price History ───────────────────────────────────────────────── #
        layout.addWidget(self._section_header("Price History"))
        layout.addWidget(QLabel(
            "Snapshot today's EUR prices for all cards into the price history database.\n"
            "This happens automatically on startup — use this button for a manual snapshot."
        ))
        price_row = QHBoxLayout()
        self._record_prices_btn = QPushButton("📸 Record price snapshot")
        self._record_prices_btn.setToolTip(
            "Save today's prices for all cards (INSERT OR IGNORE — safe to run multiple times)"
        )
        price_row.addWidget(self._record_prices_btn)
        price_row.addStretch()
        layout.addLayout(price_row)
        self._price_status = QLabel("")
        self._price_status.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._price_status)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Cardmarket Prices ───────────────────────────────────────────── #
        layout.addWidget(self._section_header("Cardmarket Prices"))
        layout.addWidget(QLabel(
            "Download the daily Cardmarket price guide (~25 MB) and cache it locally.\n"
            "Trend prices are then shown alongside Scryfall prices in the card detail panel."
        ))
        cm_row = QHBoxLayout()
        self._cm_sync_btn = QPushButton("↻  Sync CM prices")
        self._cm_backfill_btn = QPushButton("↻  Backfill CM IDs")
        self._cm_backfill_btn.setToolTip(
            "Fetch Scryfall data for collection cards that are missing a Cardmarket ID."
        )
        cm_row.addWidget(self._cm_sync_btn)
        cm_row.addWidget(self._cm_backfill_btn)
        cm_row.addStretch()
        layout.addLayout(cm_row)
        self._cm_status = QLabel("")
        self._cm_status.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._cm_status)

        self._cm_sync_btn.clicked.connect(self._on_sync_cm)
        self._cm_backfill_btn.clicked.connect(self._on_backfill_cm_ids)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Competitive Meta Data ────────────────────────────────────────── #
        layout.addWidget(self._section_header("Competitive Meta Data"))
        layout.addWidget(QLabel(
            "Crawl mtgtop8.com for competitive deck data.\n"
            "Card scores derived from these decks improve deck builder suggestions."
        ))

        from PyQt6.QtWidgets import QSpinBox
        meta_fmt_box = QGroupBox("Formats to crawl")
        meta_fmt_layout = QHBoxLayout(meta_fmt_box)
        meta_fmt_layout.setSpacing(6)
        self._meta_format_cbs: dict[str, "QCheckBox"] = {}
        for code, label in [
            ("LE", "Legacy"), ("MO", "Modern"), ("ST", "Standard"),
            ("VI", "Vintage"), ("PAU", "Pauper"), ("EDH", "Commander"), ("PI", "Pioneer"),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(code in ("LE", "MO", "ST", "PI"))
            cb.setProperty("meta_code", code)
            meta_fmt_layout.addWidget(cb)
            self._meta_format_cbs[code] = cb
        meta_fmt_layout.addStretch()
        layout.addWidget(meta_fmt_box)

        meta_opt_row = QHBoxLayout()
        meta_opt_row.addWidget(QLabel("Max events per format:"))
        self._meta_events_sb = QSpinBox()
        self._meta_events_sb.setRange(1, 100)
        self._meta_events_sb.setValue(15)
        self._meta_events_sb.setFixedWidth(64)
        meta_opt_row.addWidget(self._meta_events_sb)
        meta_opt_row.addSpacing(16)
        self._meta_mtgo_cb = QCheckBox("MTGO")
        self._meta_mtgo_cb.setChecked(True)
        self._meta_paper_cb = QCheckBox("Paper")
        self._meta_paper_cb.setChecked(True)
        meta_opt_row.addWidget(self._meta_mtgo_cb)
        meta_opt_row.addWidget(self._meta_paper_cb)
        meta_opt_row.addStretch()
        layout.addLayout(meta_opt_row)

        meta_btn_row = QHBoxLayout()
        self._meta_crawl_btn  = QPushButton("🌐 Update meta")
        self._meta_clear_btn  = QPushButton("🗑 Clear all meta data")
        self._meta_clear_btn.setToolTip("Delete all crawled meta decks and scores from the database")
        meta_btn_row.addWidget(self._meta_crawl_btn)
        meta_btn_row.addWidget(self._meta_clear_btn)
        meta_btn_row.addStretch()
        layout.addLayout(meta_btn_row)

        self._meta_progress = QProgressBar()
        self._meta_progress.setTextVisible(False)
        self._meta_progress.setFixedHeight(8)
        self._meta_progress.setVisible(False)
        layout.addWidget(self._meta_progress)

        self._meta_status = QLabel("")
        self._meta_status.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._meta_status)

        self._meta_crawl_btn.clicked.connect(self._on_meta_crawl)
        self._meta_clear_btn.clicked.connect(self._on_meta_clear)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Export Collection ───────────────────────────────────────────── #
        layout.addWidget(self._section_header("Export Collection"))
        layout.addWidget(QLabel(
            "Save your collection to a file. Moxfield CSV is recommended for compatibility."
        ))
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
        self._exp_status.setStyleSheet("color: #888;")
        layout.addWidget(self._exp_status)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Import Cards ────────────────────────────────────────────────── #
        layout.addWidget(self._section_header("Import Cards"))
        layout.addWidget(QLabel(
            "Import from a Moxfield CSV, full CSV export, or JSON export."
        ))
        import_row = QHBoxLayout()
        self._imp_btn = QPushButton("Choose file…")
        import_row.addWidget(self._imp_btn)
        import_row.addStretch()
        layout.addLayout(import_row)

        self._imp_status = QLabel("No file selected.")
        self._imp_status.setStyleSheet("color: #888;")
        layout.addWidget(self._imp_status)

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

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Backup & Restore ────────────────────────────────────────────── #
        layout.addWidget(self._section_header("Create Backup"))
        layout.addWidget(QLabel(
            "Save the current database to a .db file. Use this to create a manual backup."
        ))

        # Default backup directory
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Default directory:"))
        self._backup_dir_edit = QLineEdit()
        self._backup_dir_edit.setPlaceholderText("(none — dialog opens at last location)")
        import core.config as _cfg_local
        self._backup_dir_edit.setText(_cfg_local.get_app().get("backup_dir", ""))
        dir_row.addWidget(self._backup_dir_edit, stretch=1)
        self._backup_dir_browse_btn = QPushButton("Browse…")
        self._backup_dir_browse_btn.setFixedWidth(80)
        dir_row.addWidget(self._backup_dir_browse_btn)
        self._backup_dir_save_btn = QPushButton("Save")
        self._backup_dir_save_btn.setFixedWidth(60)
        dir_row.addWidget(self._backup_dir_save_btn)
        layout.addLayout(dir_row)
        self._backup_dir_status = QLabel("")
        self._backup_dir_status.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._backup_dir_status)

        backup_row = QHBoxLayout()
        self._backup_btn = QPushButton("Save backup…")
        backup_row.addWidget(self._backup_btn)
        backup_row.addStretch()
        layout.addLayout(backup_row)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        layout.addWidget(self._section_header("Restore Backup"))
        layout.addWidget(QLabel(
            "Replace the current database with a backup file (.db, .db.gz, .db.xz).\n"
            "⚠  This will overwrite all current data."
        ))
        restore_row = QHBoxLayout()
        self._restore_btn = QPushButton("Restore backup…")
        restore_row.addWidget(self._restore_btn)
        restore_row.addStretch()
        layout.addLayout(restore_row)

        self._backup_status = QLabel("")
        self._backup_status.setStyleSheet("color: #888;")
        layout.addWidget(self._backup_status)

        layout.addStretch()

        # Signals
        self._sync_missing_btn.clicked.connect(self._on_sync_missing)
        self._sync_full_btn.clicked.connect(self._on_sync_full)
        self._sync_lang_btn.clicked.connect(self._on_sync_lang)
        self._sync_dfc_btn.clicked.connect(self._on_sync_dfc)
        self._sync_cancel_btn.clicked.connect(self._on_sync_cancel)
        self._record_prices_btn.clicked.connect(self._on_record_prices)
        self._backup_dir_browse_btn.clicked.connect(self._on_browse_backup_dir)
        self._backup_dir_save_btn.clicked.connect(self._on_save_backup_dir)
        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    # ------------------------------------------------------------------ #
    # Environment tab                                                       #
    # ------------------------------------------------------------------ #

    def _build_env_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        self._env_fields: dict[str, QLineEdit] = {}

        current = self._read_config_values()

        for group_title, fields in _CONFIG_GROUPS:
            box = QGroupBox(group_title)
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)

            for key, label, tooltip, is_secret in fields:
                edit = QLineEdit()
                edit.setText(current.get(key, ""))
                edit.setToolTip(tooltip)
                edit.setPlaceholderText("(not set)")

                if is_secret:
                    edit.setEchoMode(QLineEdit.EchoMode.Password)
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.addWidget(edit)
                    toggle = QPushButton("Show")
                    toggle.setFixedWidth(52)
                    toggle.setCheckable(True)
                    toggle.setToolTip("Show / hide the token")
                    toggle.toggled.connect(
                        lambda checked, e=edit, b=toggle: (
                            e.setEchoMode(
                                QLineEdit.EchoMode.Normal if checked
                                else QLineEdit.EchoMode.Password
                            ),
                            b.setText("Hide" if checked else "Show"),
                        )
                    )
                    row_layout.addWidget(toggle)
                    field_widget = row_widget
                else:
                    field_widget = edit

                lbl = QLabel(label)
                lbl.setToolTip(tooltip)
                form.addRow(lbl, field_widget)
                self._env_fields[key] = edit

            layout.addWidget(box)

        # Save button + status
        save_row = QHBoxLayout()
        self._env_save_btn = QPushButton("Save config.json")
        self._env_save_btn.setFixedWidth(140)
        self._env_status = QLabel("")
        self._env_status.setStyleSheet("color: #888;")
        save_row.addWidget(self._env_save_btn)
        save_row.addWidget(self._env_status)
        save_row.addStretch()
        layout.addLayout(save_row)
        layout.addStretch()

        self._env_save_btn.clicked.connect(self._on_save_config)

        scroll.setWidget(inner)
        outer_layout.addWidget(scroll)
        return outer

    @staticmethod
    def _read_config_values() -> dict[str, str]:
        """Return flat {dotted_key: str_value} from config.json."""
        import core.config as _cfg
        config = _cfg.load()
        flat: dict[str, str] = {}
        for section_key in ("discord", "app"):
            section = config.get(section_key, {})
            for k, v in section.items():
                flat[f"{section_key}.{k}"] = str(v) if v is not None else ""
        return flat

    def _on_save_config(self):
        import core.config as _cfg
        config = _cfg.load()
        for group_title, fields in _CONFIG_GROUPS:
            for dotted_key, _label, _tooltip, _secret in fields:
                section, _, key = dotted_key.partition(".")
                val = self._env_fields[dotted_key].text().strip()
                config.setdefault(section, {})[key] = val
        try:
            _cfg.save(config)
            self._env_status.setText("Saved to config.json")
            self._env_status.setStyleSheet("color: #4caf50;")
        except Exception as exc:
            self._env_status.setText(f"Error: {exc}")
            self._env_status.setStyleSheet("color: #e94560;")

    # ------------------------------------------------------------------ #
    # Containers & Overcount tab                                           #
    # ------------------------------------------------------------------ #

    @property
    def _DEFAULT_TYPES(self) -> list[str]:  # noqa: N802
        import core.config as _cfg
        return _cfg.BUILTIN_TYPES

    def _build_containers_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(16)

        config = cfg.load()

        # ---- Container types ----
        layout.addWidget(self._section_header("Container Types"))
        layout.addWidget(QLabel(
            "Default types cannot be removed. Custom types can be added below."
        ))

        self._types_list = QListWidget()
        self._types_list.setMaximumHeight(160)
        for t in config.get("container_types", self._DEFAULT_TYPES):
            self._add_type_item(t, is_default=t in self._DEFAULT_TYPES)
        layout.addWidget(self._types_list)

        add_row = QHBoxLayout()
        self._new_type_edit = QLineEdit()
        self._new_type_edit.setPlaceholderText("New type name…")
        self._add_type_btn = QPushButton("Add")
        self._add_type_btn.setFixedWidth(64)
        self._remove_type_btn = QPushButton("Remove selected")
        add_row.addWidget(self._new_type_edit)
        add_row.addWidget(self._add_type_btn)
        add_row.addWidget(self._remove_type_btn)
        add_row.addStretch()
        layout.addLayout(add_row)

        layout.addWidget(self._divider())

        # ---- Overcount exclusions ----
        layout.addWidget(self._section_header("Overcount Exclusions"))
        layout.addWidget(QLabel(
            "Cards in containers of the selected types are not counted in the Overcount scan."
        ))

        self._excl_checkboxes: dict[str, QCheckBox] = {}
        self._excl_box = QGroupBox()
        self._excl_layout = QVBoxLayout(self._excl_box)
        self._excl_layout.setSpacing(4)
        self._rebuild_excl_checkboxes(
            config.get("container_types", self._DEFAULT_TYPES),
            config.get("overcount_excluded_types", []),
        )
        layout.addWidget(self._excl_box)

        layout.addWidget(self._divider())

        # ---- Save ----
        save_row = QHBoxLayout()
        self._ct_save_btn = QPushButton("Save")
        self._ct_save_btn.setFixedWidth(100)
        self._ct_status = QLabel("")
        self._ct_status.setStyleSheet("color: #888;")
        save_row.addWidget(self._ct_save_btn)
        save_row.addWidget(self._ct_status)
        save_row.addStretch()
        layout.addLayout(save_row)
        layout.addStretch()

        # Signals
        self._add_type_btn.clicked.connect(self._on_add_type)
        self._new_type_edit.returnPressed.connect(self._on_add_type)
        self._remove_type_btn.clicked.connect(self._on_remove_type)
        self._ct_save_btn.clicked.connect(self._on_save_containers)

        return tab

    def _add_type_item(self, name: str, is_default: bool):
        item = QListWidgetItem(f"{'🔒 ' if is_default else '  '}{name}")
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setData(Qt.ItemDataRole.UserRole + 1, is_default)
        self._types_list.addItem(item)

    def _current_types(self) -> list[str]:
        return [
            self._types_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._types_list.count())
        ]

    def _rebuild_excl_checkboxes(self, types: list[str], excluded: list[str]):
        while self._excl_layout.count():
            item = self._excl_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._excl_checkboxes.clear()
        for t in types:
            cb = QCheckBox(t)
            cb.setChecked(t in excluded)
            cb.toggled.connect(lambda _: None)  # no-op; read on save
            self._excl_layout.addWidget(cb)
            self._excl_checkboxes[t] = cb

    def _on_add_type(self):
        name = self._new_type_edit.text().strip().lower()
        if not name:
            return
        if name in self._current_types():
            self._ct_status.setText("Type already exists.")
            self._ct_status.setStyleSheet("color: #e94560;")
            return
        self._add_type_item(name, is_default=False)
        self._new_type_edit.clear()
        # Add checkbox
        cb = QCheckBox(name)
        self._excl_layout.addWidget(cb)
        self._excl_checkboxes[name] = cb
        self._ct_status.setText("")

    def _on_remove_type(self):
        item = self._types_list.currentItem()
        if not item:
            return
        if item.data(Qt.ItemDataRole.UserRole + 1):
            self._ct_status.setText("Default types cannot be removed.")
            self._ct_status.setStyleSheet("color: #e94560;")
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        self._types_list.takeItem(self._types_list.row(item))
        cb = self._excl_checkboxes.pop(name, None)
        if cb:
            cb.deleteLater()
        self._ct_status.setText("")

    def _on_save_containers(self):
        types = self._current_types()
        excluded = [t for t, cb in self._excl_checkboxes.items() if cb.isChecked()]
        config = cfg.load()
        config["container_types"] = types
        config["overcount_excluded_types"] = excluded
        try:
            cfg.save(config)
            self._ct_status.setText("Saved.")
            self._ct_status.setStyleSheet("color: #4caf50;")
        except Exception as exc:
            self._ct_status.setText(f"Error: {exc}")
            self._ct_status.setStyleSheet("color: #e94560;")

    # ------------------------------------------------------------------ #
    # Buylists tab                                                         #
    # ------------------------------------------------------------------ #

    def _build_buylists_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(self._section_header("Online Buylist Sources"))
        layout.addWidget(QLabel(
            "Add store buylist URLs here.\n"
            "They appear as quick-select options in the Buylists view."
        ))

        self._bl_table = QTableWidget(0, 2)
        self._bl_table.setHorizontalHeaderLabels(["Name", "URL"])
        hh = self._bl_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._bl_table.setColumnWidth(0, 200)
        self._bl_table.verticalHeader().setVisible(False)
        self._bl_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._bl_table.setAlternatingRowColors(True)
        layout.addWidget(self._bl_table)

        for src in cfg.load().get("buylist_sources", []):
            self._bl_add_row(src.get("name", ""), src.get("url", ""))

        btn_row = QHBoxLayout()
        self._bl_add_btn    = QPushButton("Add row")
        self._bl_add_btn.setFixedWidth(90)
        self._bl_remove_btn = QPushButton("Remove selected")
        self._bl_remove_btn.setFixedWidth(130)
        btn_row.addWidget(self._bl_add_btn)
        btn_row.addWidget(self._bl_remove_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        save_row = QHBoxLayout()
        self._bl_save_btn = QPushButton("Save")
        self._bl_save_btn.setFixedWidth(90)
        self._bl_status   = QLabel("")
        self._bl_status.setStyleSheet("color: #888;")
        save_row.addWidget(self._bl_save_btn)
        save_row.addWidget(self._bl_status)
        save_row.addStretch()
        layout.addLayout(save_row)
        layout.addStretch()

        self._bl_add_btn.clicked.connect(self._on_bl_add_row)
        self._bl_remove_btn.clicked.connect(self._on_bl_remove_row)
        self._bl_save_btn.clicked.connect(self._on_bl_save)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Brave Web Search ──────────────────────────────────────────── #
        layout.addWidget(self._section_header("Brave Web Search"))
        layout.addWidget(QLabel(
            "Use the Brave Search API to automatically discover buylist pages.\n"
            "Get a free API key at https://brave.com/search/api/"
        ))

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("API Key:"))
        self._brave_key_edit = QLineEdit()
        self._brave_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._brave_key_edit.setPlaceholderText("BSA…")
        self._brave_key_edit.setText(cfg.load().get("brave", {}).get("api_key", ""))
        key_row.addWidget(self._brave_key_edit, stretch=1)
        layout.addLayout(key_row)

        layout.addWidget(QLabel("Search keywords (one per line):"))
        self._brave_kw_edit = QPlainTextEdit()
        self._brave_kw_edit.setMaximumHeight(90)
        self._brave_kw_edit.setPlaceholderText("MTG Karten Ankauf Buylist\nMagic cards buylist kaufen")
        kws = cfg.load().get("brave", {}).get("keywords", [])
        self._brave_kw_edit.setPlainText("\n".join(kws))
        layout.addWidget(self._brave_kw_edit)

        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("Max results per keyword:"))
        from PyQt6.QtWidgets import QSpinBox
        self._brave_max_sb = QSpinBox()
        self._brave_max_sb.setRange(1, 30)
        self._brave_max_sb.setValue(cfg.load().get("brave", {}).get("max_results", 15))
        res_row.addWidget(self._brave_max_sb)
        res_row.addStretch()
        layout.addLayout(res_row)

        brave_save_row = QHBoxLayout()
        self._brave_save_btn = QPushButton("Save")
        self._brave_save_btn.setFixedWidth(90)
        self._brave_status = QLabel("")
        self._brave_status.setStyleSheet("color: #888;")
        brave_save_row.addWidget(self._brave_save_btn)
        brave_save_row.addWidget(self._brave_status)
        brave_save_row.addStretch()
        layout.addLayout(brave_save_row)
        layout.addStretch()

        self._brave_save_btn.clicked.connect(self._on_brave_save)

        layout.addSpacing(4)
        layout.addWidget(self._divider())
        layout.addSpacing(4)

        # ── Store Login Credentials ───────────────────────────────────────── #
        layout.addWidget(self._section_header("Store Login Credentials"))
        layout.addWidget(QLabel(
            "Save login details for stores that require an account to view their buylist.\n"
            "The webcrawler will auto-detect the login form and POST your credentials.\n"
            "⚠ Stored in plaintext in config.json — do not use for sensitive accounts."
        ))

        self._creds_table = QTableWidget(0, 3)
        self._creds_table.setHorizontalHeaderLabels(["Domain", "Username", "Login URL"])
        ch = self._creds_table.horizontalHeader()
        ch.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        ch.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._creds_table.setColumnWidth(0, 180)
        self._creds_table.setColumnWidth(1, 140)
        self._creds_table.verticalHeader().setVisible(False)
        self._creds_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._creds_table.setAlternatingRowColors(True)
        self._creds_table.setMaximumHeight(150)
        layout.addWidget(self._creds_table)

        for cred in cfg.load().get("store_credentials", []):
            self._creds_add_row(
                cred.get("domain", ""),
                cred.get("username", ""),
                cred.get("login_url", ""),
            )

        creds_btn_row = QHBoxLayout()
        self._creds_remove_btn = QPushButton("Remove selected")
        self._creds_remove_btn.setFixedWidth(140)
        self._creds_save_btn   = QPushButton("Save")
        self._creds_save_btn.setFixedWidth(90)
        self._creds_status     = QLabel("")
        self._creds_status.setStyleSheet("color: #888;")
        creds_btn_row.addWidget(self._creds_remove_btn)
        creds_btn_row.addWidget(self._creds_save_btn)
        creds_btn_row.addWidget(self._creds_status)
        creds_btn_row.addStretch()
        layout.addLayout(creds_btn_row)
        layout.addWidget(QLabel(
            "<small>💡 To add credentials: right-click a store in Buylists → Web Search → "
            "<i>Store credentials…</i></small>"
        ))
        layout.addStretch()

        self._creds_remove_btn.clicked.connect(self._on_creds_remove_row)
        self._creds_save_btn.clicked.connect(self._on_creds_save)

        return tab

    def _creds_add_row(self, domain: str = "", username: str = "", login_url: str = ""):
        row = self._creds_table.rowCount()
        self._creds_table.insertRow(row)
        self._creds_table.setItem(row, 0, QTableWidgetItem(domain))
        self._creds_table.setItem(row, 1, QTableWidgetItem(username))
        self._creds_table.setItem(row, 2, QTableWidgetItem(login_url))

    def _on_creds_remove_row(self):
        rows = sorted(
            {idx.row() for idx in self._creds_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._creds_table.removeRow(row)

    def _on_creds_save(self):
        creds: list[dict] = []
        # Preserve passwords — load existing creds and match by domain
        existing = {c["domain"]: c for c in cfg.load().get("store_credentials", [])}
        for row in range(self._creds_table.rowCount()):
            d_item = self._creds_table.item(row, 0)
            u_item = self._creds_table.item(row, 1)
            l_item = self._creds_table.item(row, 2)
            domain    = d_item.text().strip() if d_item else ""
            username  = u_item.text().strip() if u_item else ""
            login_url = l_item.text().strip() if l_item else ""
            if not domain:
                continue
            # Re-use saved password (table doesn't show it)
            password = existing.get(domain, {}).get("password", "")
            creds.append({
                "domain":    domain,
                "username":  username,
                "password":  password,
                "login_url": login_url,
            })
        config = cfg.load()
        config["store_credentials"] = creds
        try:
            cfg.save(config)
            self._creds_status.setText("Saved.")
            self._creds_status.setStyleSheet("color: #4caf50;")
        except Exception as exc:
            self._creds_status.setText(f"Error: {exc}")
            self._creds_status.setStyleSheet("color: #e94560;")

    def _on_brave_save(self):
        kws = [
            l.strip() for l in self._brave_kw_edit.toPlainText().splitlines()
            if l.strip()
        ]
        config = cfg.load()
        config.setdefault("brave", {})
        config["brave"]["api_key"]     = self._brave_key_edit.text().strip()
        config["brave"]["keywords"]    = kws
        config["brave"]["max_results"] = self._brave_max_sb.value()
        try:
            cfg.save(config)
            self._brave_status.setText("Saved.")
            self._brave_status.setStyleSheet("color: #4caf50;")
        except Exception as exc:
            self._brave_status.setText(f"Error: {exc}")
            self._brave_status.setStyleSheet("color: #e94560;")

    def _bl_add_row(self, name: str = "", url: str = ""):
        row = self._bl_table.rowCount()
        self._bl_table.insertRow(row)
        self._bl_table.setItem(row, 0, QTableWidgetItem(name))
        self._bl_table.setItem(row, 1, QTableWidgetItem(url))

    def _on_bl_add_row(self):
        self._bl_add_row()
        row = self._bl_table.rowCount() - 1
        self._bl_table.setCurrentCell(row, 0)
        self._bl_table.editItem(self._bl_table.item(row, 0))

    def _on_bl_remove_row(self):
        rows = sorted(
            {idx.row() for idx in self._bl_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self._bl_table.removeRow(row)

    def _on_bl_save(self):
        sources = []
        for row in range(self._bl_table.rowCount()):
            name_item = self._bl_table.item(row, 0)
            url_item  = self._bl_table.item(row, 1)
            name = name_item.text().strip() if name_item else ""
            url  = url_item.text().strip()  if url_item  else ""
            if name or url:
                sources.append({"name": name, "url": url})
        config = cfg.load()
        config["buylist_sources"] = sources
        try:
            cfg.save(config)
            self._bl_status.setText("Saved.")
            self._bl_status.setStyleSheet("color: #4caf50;")
        except Exception as exc:
            self._bl_status.setText(f"Error: {exc}")
            self._bl_status.setStyleSheet("color: #e94560;")

    @staticmethod
    def _section_header(text: str) -> QLabel:
        lbl = QLabel(f"<b>{text}</b>")
        lbl.setStyleSheet("font-size: 14px;")
        return lbl

    @staticmethod
    def _divider() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
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
    # Sync & Maintenance                                                    #
    # ------------------------------------------------------------------ #

    def _set_sync_busy(self, busy: bool):
        for btn in (self._sync_missing_btn, self._sync_full_btn, self._sync_lang_btn, self._sync_dfc_btn):
            btn.setEnabled(not busy)
        self._sync_progress.setVisible(busy)
        self._sync_cancel_btn.setVisible(busy)
        if not busy:
            self._sync_cancel_btn.setEnabled(True)

    def _on_sync_cancel(self):
        self._sync_cancelled = True
        self._sync_cancel_btn.setEnabled(False)

    def _on_sync_missing(self):
        self._run_sync(mode="missing")

    def _on_sync_full(self):
        reply = QMessageBox.question(
            self, "Full reload",
            "This will re-fetch ALL cards from Scryfall.\n"
            "Depending on collection size this may take several minutes.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._run_sync(mode="full")

    def _on_sync_lang(self):
        self._run_sync(mode="lang")

    def _on_sync_dfc(self):
        self._run_sync(mode="dfc")

    @asyncSlot()
    async def _run_sync(self, mode: str):
        from desktop.db import db, scryfall

        self._sync_cancelled = False
        self._set_sync_busy(True)
        self._sync_status.setText("Preparing…")
        self._sync_progress.setValue(0)

        try:
            if mode == "missing":
                ids = await db.get_scryfall_ids_missing_data()
                label = "Missing data"
                await self._run_id_sync(ids, label, db, scryfall)
            elif mode == "full":
                ids = await db.get_distinct_scryfall_ids()
                label = "Full reload"
                await self._run_id_sync(ids, label, db, scryfall)
            elif mode == "lang":
                await self._run_lang_fix(db, scryfall)
            elif mode == "dfc":
                await self._run_dfc_fix(db, scryfall)
        finally:
            self._set_sync_busy(False)

    async def _run_id_sync(self, ids: list, label: str, db, scryfall):
        total = len(ids)
        if total == 0:
            self._sync_status.setText("✓ All cards already up to date.")
            self._sync_progress.setRange(0, 1)
            self._sync_progress.setValue(1)
            return

        self._sync_progress.setRange(0, total)
        updated = failed = 0

        for i, sid in enumerate(ids):
            if self._sync_cancelled:
                break
            self._sync_status.setText(f"{label}: {i + 1} / {total}")
            self._sync_progress.setValue(i + 1)
            try:
                card = await scryfall.get_by_id(sid)
                if card:
                    await db.resync_card(sid, card)
                    updated += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        await db.record_prices()

        if self._sync_cancelled:
            remaining = total - updated - failed
            self._sync_status.setText(
                f"Cancelled — {updated} updated, {remaining} remaining."
            )
        else:
            parts = [f"✓ {updated} updated"]
            if failed:
                parts.append(f"{failed} failed")
            self._sync_status.setText("  ·  ".join(parts))

    async def _run_lang_fix(self, db, scryfall):
        rows = await db.get_cards_needing_lang_fix()
        total = len(rows)

        if total == 0:
            self._sync_status.setText("✓ All non-English cards already have localized data.")
            self._sync_progress.setRange(0, 1)
            self._sync_progress.setValue(1)
            return

        self._sync_progress.setRange(0, total)
        updated = failed = skipped = 0

        for i, row in enumerate(rows):
            if self._sync_cancelled:
                break
            lang = row["language"]
            name = row["name_en"] or ""
            self._sync_status.setText(
                f"Language fix: {i + 1} / {total}  ({lang.upper()}: {name})"
            )
            self._sync_progress.setValue(i + 1)

            try:
                card = None
                if row.get("set_code") and row.get("collector_number"):
                    card = await scryfall.get_by_collector(
                        row["set_code"], row["collector_number"], lang
                    )
                # Fallback: re-fetch by scryfall_id (works if stored ID is already localized)
                if card is None and row.get("scryfall_id"):
                    card = await scryfall.get_by_id(row["scryfall_id"])
                    if card and card.get("language", "en") == "en":
                        card = None  # got English data — not useful

                if card and card.get("printed_name") and card["printed_name"] != row["name_en"]:
                    await db.fix_card_lang_data(row["id"], card)
                    updated += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1

        parts = [f"✓ {updated} fixed"]
        if skipped:
            parts.append(f"{skipped} skipped (no localized data on Scryfall)")
        if failed:
            parts.append(f"{failed} errors")
        self._sync_status.setText("  ·  ".join(parts))

    async def _run_dfc_fix(self, db, scryfall):
        """Re-fetch double-faced cards missing mana_cost or image_url_back."""
        rows = await db.get_cards_needing_dfc_fix()
        total = len(rows)

        if total == 0:
            self._sync_status.setText("✓ All double-faced cards already have complete data.")
            self._sync_progress.setRange(0, 1)
            self._sync_progress.setValue(1)
            return

        self._sync_progress.setRange(0, total)
        updated = failed = 0

        for i, row in enumerate(rows):
            if self._sync_cancelled:
                break
            name = row["name_en"] or ""
            self._sync_status.setText(f"DFC fix: {i + 1} / {total}  ({name})")
            self._sync_progress.setValue(i + 1)
            try:
                card = await scryfall.get_by_id(row["scryfall_id"])
                if card:
                    await db.resync_card(row["scryfall_id"], card)
                    updated += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        parts = [f"✓ {updated} DFC(s) updated"]
        if failed:
            parts.append(f"{failed} errors")
        self._sync_status.setText("  ·  ".join(parts))

    # ------------------------------------------------------------------ #
    # Competitive Meta                                                      #
    # ------------------------------------------------------------------ #

    async def _load_meta_stats(self):
        try:
            from desktop.db import db
            stats = await db.get_meta_stats()
            total = stats.get("total_decks", 0)
            last  = (stats.get("last_crawl") or "")[:10]
            score_rows = stats.get("score_rows", 0)
            fmt_parts = [f"{fmt}: {n}" for fmt, n in sorted(stats.get("deck_counts", {}).items())]
            if total:
                detail = "  |  ".join(fmt_parts) if fmt_parts else ""
                self._meta_status.setText(
                    f"{total} deck(s) stored  ·  {score_rows} card scores"
                    + (f"  ·  last crawl: {last}" if last else "")
                    + (f"\n{detail}" if detail else "")
                )
            else:
                self._meta_status.setText("No meta data yet — click 'Update meta' to crawl.")
        except Exception as exc:
            self._meta_status.setText(f"Error loading meta stats: {exc}")

    @asyncSlot()
    async def _on_meta_crawl(self):
        from desktop.db import db
        from core.meta_crawler import crawl_formats

        selected = [code for code, cb in self._meta_format_cbs.items() if cb.isChecked()]
        if not selected:
            QMessageBox.warning(self, "Meta crawl", "Select at least one format to crawl.")
            return

        self._meta_crawl_btn.setEnabled(False)
        self._meta_clear_btn.setEnabled(False)
        self._meta_progress.setVisible(True)
        self._meta_progress.setRange(0, 0)  # indeterminate
        max_events = self._meta_events_sb.value()
        include_mtgo  = self._meta_mtgo_cb.isChecked()
        include_paper = self._meta_paper_cb.isChecked()

        if not include_mtgo and not include_paper:
            QMessageBox.warning(self, "Meta crawl", "Select at least MTGO or Paper.")
            self._meta_crawl_btn.setEnabled(True)
            self._meta_clear_btn.setEnabled(True)
            self._meta_progress.setVisible(False)
            return

        total_saved = 0

        def _cb(msg: str, done: int, total: int):
            self._meta_status.setText(msg)
            if total > 0:
                self._meta_progress.setRange(0, total)
                self._meta_progress.setValue(done)

        try:
            self._meta_status.setText(f"Crawling {', '.join(selected)}…")
            total_saved = await crawl_formats(
                db, selected,
                max_events=max_events,
                include_mtgo=include_mtgo,
                include_paper=include_paper,
                progress_cb=_cb,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Meta crawl error", str(exc))
            self._meta_status.setText(f"Error: {exc}")
            return
        finally:
            self._meta_crawl_btn.setEnabled(True)
            self._meta_clear_btn.setEnabled(True)
            self._meta_progress.setVisible(False)

        # Recompute scores after crawl
        self._meta_status.setText("Recomputing card scores…")
        try:
            n_scores = await db.recompute_meta_scores()
            self._meta_status.setText(
                f"✓ {total_saved} new deck(s) stored  ·  {n_scores} card scores computed."
            )
        except Exception as exc:
            self._meta_status.setText(f"✓ {total_saved} deck(s) saved  (score error: {exc})")

        await self._load_meta_stats()

    @asyncSlot()
    async def _on_meta_clear(self):
        reply = QMessageBox.question(
            self, "Clear meta data",
            "Delete ALL crawled meta decks and scores from the database?\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from desktop.db import db
            n = await db.clear_meta_data()
            self._meta_status.setText(f"✓ Cleared {n} meta deck(s).")
        except Exception as exc:
            self._meta_status.setText(f"Error: {exc}")

    @asyncSlot()
    async def _on_record_prices(self):
        self._record_prices_btn.setEnabled(False)
        self._price_status.setText("Recording…")
        try:
            from desktop.db import db
            n = await db.record_prices()
            self._price_status.setText(f"✓ Snapshotted today's price for {n} card(s).")
        except Exception as exc:
            self._price_status.setText(f"Error: {exc}")
        finally:
            self._record_prices_btn.setEnabled(True)

    def db_ready(self):
        """Called when the database is ready — populate CM price status and meta stats."""
        import asyncio
        asyncio.ensure_future(self._load_cm_meta())
        asyncio.ensure_future(self._load_meta_stats())

    async def _load_cm_meta(self):
        try:
            from desktop.db import db
            meta = await db.get_cm_prices_meta()
            count = meta.get("count", 0)
            updated_at = meta.get("updated_at")
            if count and updated_at:
                self._cm_status.setText(
                    f"Last sync: {updated_at[:10]}  ({count:,} entries cached)"
                )
            elif count:
                self._cm_status.setText(f"{count:,} entries cached")
            else:
                self._cm_status.setText("No CM prices cached yet.")
        except Exception as exc:
            self._cm_status.setText(f"Error loading CM meta: {exc}")

    def _cm_progress(self, msg: str, done: int, total: int):
        self._cm_status.setText(msg)

    @asyncSlot()
    async def _on_sync_cm(self):
        from desktop.db import db
        self._cm_sync_btn.setEnabled(False)
        self._cm_status.setText("Starting…")
        try:
            n = await db.sync_cm_prices(progress_cb=self._cm_progress)
            self._cm_status.setText(f"✓ Synced {n:,} CM prices.")
        except Exception as exc:
            self._cm_status.setText(f"Error: {exc}")
        finally:
            self._cm_sync_btn.setEnabled(True)

    @asyncSlot()
    async def _on_backfill_cm_ids(self):
        import logging as _log
        _logger = _log.getLogger(__name__)
        from desktop.db import db, scryfall
        self._cm_backfill_btn.setEnabled(False)
        try:
            ids = await db.get_scryfall_ids_missing_cm_id()
            total = len(ids)
            if total == 0:
                self._cm_status.setText("✓ All collection cards already have a CM ID.")
                return
            self._cm_status.setText(f"Backfilling 0 / {total}…")
            rows_updated = 0   # actual DB rows changed
            no_cm_id    = 0   # Scryfall has no CM ID for this card
            errors      = 0
            for i, sid in enumerate(ids, 1):
                try:
                    data = await scryfall.get_by_id(sid)
                    if data is None:
                        errors += 1
                        _logger.warning("CM backfill: Scryfall returned None for %s", sid)
                    elif data.get("cardmarket_id"):
                        changed = await db.update_card_cm_id(sid, data["cardmarket_id"])
                        rows_updated += changed
                        if changed == 0:
                            _logger.debug(
                                "CM backfill: update_card_cm_id(%s, %s) matched 0 rows",
                                sid, data["cardmarket_id"],
                            )
                    else:
                        no_cm_id += 1
                        _logger.debug("CM backfill: no cardmarket_id on Scryfall for %s", sid)
                except Exception as exc:
                    errors += 1
                    _logger.warning("CM backfill error for %s: %s", sid, exc)
                if i % 5 == 0 or i == total:
                    self._cm_status.setText(f"Backfilling {i} / {total}…")

            parts = [f"✓ {rows_updated} row(s) updated"]
            if no_cm_id:
                parts.append(f"{no_cm_id} card(s) have no CM ID on Scryfall")
            if errors:
                parts.append(f"{errors} error(s) — see Logs tab")
            self._cm_status.setText("  ·  ".join(parts))
        except Exception as exc:
            self._cm_status.setText(f"Error: {exc}")
        finally:
            self._cm_backfill_btn.setEnabled(True)

    # ------------------------------------------------------------------ #
    # Backup                                                                #
    # ------------------------------------------------------------------ #

    def _on_browse_backup_dir(self):
        current = self._backup_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select backup directory", current)
        if chosen:
            self._backup_dir_edit.setText(chosen)
            self._backup_dir_status.setText("")

    def _on_save_backup_dir(self):
        directory = self._backup_dir_edit.text().strip()
        if directory and not Path(directory).is_dir():
            self._backup_dir_status.setText("⚠ Directory does not exist.")
            self._backup_dir_status.setStyleSheet("color: #e94560; font-size: 11px;")
            return
        config = cfg.load()
        config.setdefault("app", {})["backup_dir"] = directory
        try:
            cfg.save(config)
            self._backup_dir_status.setText("Saved.")
            self._backup_dir_status.setStyleSheet("color: #4caf50; font-size: 11px;")
        except Exception as exc:
            self._backup_dir_status.setText(f"Error: {exc}")
            self._backup_dir_status.setStyleSheet("color: #e94560; font-size: 11px;")

    @asyncSlot()
    async def _on_backup(self):
        from datetime import date
        from desktop.db import db

        from core.config import DATA_DIR
        default_dir = cfg.get_app().get("backup_dir", "").strip()
        if default_dir and not Path(default_dir).is_absolute():
            default_dir = str(DATA_DIR / default_dir)
        default_name = f"mtg_backup_{date.today()}.db"
        default_path = str(Path(default_dir) / default_name) if default_dir else default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "Save backup", default_path, "Database (*.db)"
        )
        if not path:
            return

        self._backup_status.setText("Creating backup…")
        try:
            data = await db.backup_bytes()
            await asyncio.to_thread(lambda: open(path, "wb").write(data))
            self._backup_status.setText(f"Backup saved to {path}")
            QMessageBox.information(self, "Backup", f"Backup created successfully:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Backup error", str(exc))
            self._backup_status.setText("Backup failed.")

    @asyncSlot()
    async def _on_restore(self):
        import gzip
        import lzma
        from desktop.db import db

        path, _ = QFileDialog.getOpenFileName(
            self, "Open backup", "",
            "Backup files (*.db *.db.gz *.db.xz);;All files (*)"
        )
        if not path:
            return

        try:
            raw = await asyncio.to_thread(lambda: open(path, "rb").read())
            if path.endswith(".xz"):
                data = await asyncio.to_thread(lzma.decompress, raw)
            elif path.endswith(".gz"):
                data = await asyncio.to_thread(gzip.decompress, raw)
            else:
                data = raw
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
