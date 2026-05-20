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


def _webui_launch() -> tuple[str, list[str], str]:
    """Return (program, args, cwd) for launching the Web UI subprocess."""
    if _is_bundled():
        cwd = str(Path(sys.executable).parent)
        return sys.executable, ['--run-webui'], cwd
    python = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
    return python, ['-m', 'server.ui.app'], str(_PROJECT_ROOT)

_DEFAULT_UI_PORT = "8080"
_DEFAULT_UI_HOST = "0.0.0.0"

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
        ("app.backup_dir",         "Backup Directory",
         "Directory where backup files are stored (relative to project root or absolute).",
         False),
        ("app.price_source",       "Price Source",
         "Price data source: 'cardmarket' or 'scryfall'.",
         False),
        ("app.debug_scan_preview", "Debug Scan Preview",
         "Set to 1 to send a debug preview image after each scan. Keep at 0 in production.",
         False),
    ]),
    ("Web Server", [
        ("app.ui_port", "Port",
         "Port the web UI server listens on (default: 8080).",
         False),
        ("app.ui_host", "Host",
         "Host/IP the web UI server binds to (default: 0.0.0.0).",
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
        self._webui_process: QProcess | None = None
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

        # ── Web UI Server ────────────────────────────────────────────── #
        webui_box = QGroupBox("Web UI Server")
        webui_layout = QVBoxLayout(webui_box)
        webui_layout.setSpacing(8)

        webui_status_row = QHBoxLayout()
        webui_status_row.addWidget(QLabel("Status:"))
        self._webui_status_lbl = QLabel("○ Stopped")
        self._webui_status_lbl.setStyleSheet("font-weight: bold; color: #888;")
        webui_status_row.addWidget(self._webui_status_lbl)
        webui_status_row.addStretch()
        webui_layout.addLayout(webui_status_row)

        webui_url_row = QHBoxLayout()
        webui_url_row.addWidget(QLabel("URL:"))
        self._webui_url_lbl = QLabel("")
        self._webui_url_lbl.setStyleSheet("color: #89b4fa;")
        webui_url_row.addWidget(self._webui_url_lbl)
        self._webui_copy_btn = QPushButton("Copy")
        self._webui_copy_btn.setFixedWidth(54)
        self._webui_copy_btn.setEnabled(False)
        webui_url_row.addWidget(self._webui_copy_btn)
        webui_url_row.addStretch()
        webui_layout.addLayout(webui_url_row)

        webui_btn_row = QHBoxLayout()
        self._webui_start_btn = QPushButton("▶  Start server")
        self._webui_stop_btn  = QPushButton("■  Stop server")
        self._webui_stop_btn.setEnabled(False)
        webui_btn_row.addWidget(self._webui_start_btn)
        webui_btn_row.addWidget(self._webui_stop_btn)
        webui_btn_row.addStretch()
        webui_layout.addLayout(webui_btn_row)

        self._webui_log = QPlainTextEdit()
        self._webui_log.setReadOnly(True)
        self._webui_log.setMaximumBlockCount(_MAX_LOG_LINES)
        self._webui_log.setMaximumHeight(160)
        self._webui_log.setStyleSheet(
            "background: #0d0d1a; color: #cccccc; font-family: monospace; font-size: 11px;"
        )
        webui_layout.addWidget(self._webui_log)

        webui_log_ctrl = QHBoxLayout()
        self._webui_clear_btn = QPushButton("Clear log")
        self._webui_autoscroll_cb = QCheckBox("Auto-scroll")
        self._webui_autoscroll_cb.setChecked(True)
        webui_log_ctrl.addWidget(self._webui_clear_btn)
        webui_log_ctrl.addWidget(self._webui_autoscroll_cb)
        webui_log_ctrl.addStretch()
        webui_layout.addLayout(webui_log_ctrl)

        self._webui_start_btn.clicked.connect(self._on_webui_start)
        self._webui_stop_btn.clicked.connect(self._on_webui_stop)
        self._webui_clear_btn.clicked.connect(self._webui_log.clear)
        self._webui_copy_btn.clicked.connect(self._on_webui_copy_url)

        layout.addWidget(webui_box)
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
        """Called by MainWindow.closeEvent. Returns True if any service was running."""
        stopped_any = False
        if self._bot_process and self._bot_process.state() != QProcess.ProcessState.NotRunning:
            self._bot_process.terminate()
            stopped_any = True
        if self._webui_process and self._webui_process.state() != QProcess.ProcessState.NotRunning:
            self._webui_process.terminate()
            stopped_any = True
        return stopped_any

    # ------------------------------------------------------------------ #
    # Web UI server process control                                         #
    # ------------------------------------------------------------------ #

    def _on_webui_start(self):
        if self._webui_process and self._webui_process.state() != QProcess.ProcessState.NotRunning:
            return

        python, args, cwd = _webui_launch()

        self._webui_process = QProcess(self)
        self._webui_process.setProgram(python)
        self._webui_process.setArguments(args)
        self._webui_process.setWorkingDirectory(cwd)

        env = QProcessEnvironment.systemEnvironment()
        port_field = self._env_fields.get("app.ui_port")
        host_field = self._env_fields.get("app.ui_host")
        if port_field and port_field.text().strip():
            env.insert("UI_PORT", port_field.text().strip())
        if host_field and host_field.text().strip():
            env.insert("UI_HOST", host_field.text().strip())
        self._webui_process.setProcessEnvironment(env)

        self._webui_process.readyReadStandardOutput.connect(self._on_webui_stdout)
        self._webui_process.readyReadStandardError.connect(self._on_webui_stderr)
        self._webui_process.stateChanged.connect(self._on_webui_state_changed)
        self._webui_process.finished.connect(self._on_webui_finished)

        self._webui_process.start()

    def _on_webui_stop(self):
        if self._webui_process is None:
            return
        state = self._webui_process.state()
        if state == QProcess.ProcessState.Running:
            self._webui_process.terminate()
        elif state == QProcess.ProcessState.Starting:
            self._webui_process.kill()

    def _on_webui_stdout(self):
        if self._webui_process is None:
            return
        raw = bytes(self._webui_process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._append_webui_log(raw.rstrip())

    def _on_webui_stderr(self):
        if self._webui_process is None:
            return
        raw = bytes(self._webui_process.readAllStandardError()).decode("utf-8", errors="replace")
        self._append_webui_log(raw.rstrip(), error=True)

    def _append_webui_log(self, text: str, error: bool = False):
        if not text:
            return
        for line in text.splitlines():
            self._webui_log.appendPlainText(f"[err] {line}" if error else line)
        if self._webui_autoscroll_cb.isChecked():
            self._webui_log.verticalScrollBar().setValue(
                self._webui_log.verticalScrollBar().maximum()
            )

    def _on_webui_state_changed(self, state: QProcess.ProcessState):
        port_field = self._env_fields.get("app.ui_port") if hasattr(self, "_env_fields") else None
        port = (port_field.text().strip() if port_field else "") or str(_DEFAULT_UI_PORT)

        if state == QProcess.ProcessState.NotRunning:
            self._webui_status_lbl.setText("○ Stopped")
            self._webui_status_lbl.setStyleSheet("font-weight: bold; color: #888;")
            self._webui_start_btn.setEnabled(True)
            self._webui_stop_btn.setEnabled(False)
            self._webui_url_lbl.setText("")
            self._webui_copy_btn.setEnabled(False)
        elif state == QProcess.ProcessState.Starting:
            self._webui_status_lbl.setText("⏳ Starting…")
            self._webui_status_lbl.setStyleSheet("font-weight: bold; color: #d4af37;")
            self._webui_start_btn.setEnabled(False)
            self._webui_stop_btn.setEnabled(False)
        elif state == QProcess.ProcessState.Running:
            self._webui_status_lbl.setText("● Running")
            self._webui_status_lbl.setStyleSheet("font-weight: bold; color: #7ec8a0;")
            self._webui_start_btn.setEnabled(False)
            self._webui_stop_btn.setEnabled(True)
            self._webui_url_lbl.setText(f"http://localhost:{port}")
            self._webui_copy_btn.setEnabled(True)

    def _on_webui_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        if exit_status == QProcess.ExitStatus.CrashExit:
            self._append_webui_log("[webui] Process crashed.")
        else:
            self._append_webui_log(f"[webui] Exited with code {exit_code}.")

    def _on_webui_copy_url(self):
        from PyQt6.QtWidgets import QApplication
        url = self._webui_url_lbl.text()
        if url:
            QApplication.clipboard().setText(url)

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
        btn_grid.addWidget(self._sync_missing_btn)
        btn_grid.addWidget(self._sync_full_btn)
        btn_grid.addWidget(self._sync_lang_btn)
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
        for btn in (self._sync_missing_btn, self._sync_full_btn, self._sync_lang_btn):
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

        default_dir = cfg.get_app().get("backup_dir", "").strip()
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
