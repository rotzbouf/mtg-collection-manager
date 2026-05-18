"""Main application window."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QLabel, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from qasync import asyncSlot

from desktop.widgets.add_card import AddCardWidget
from desktop.widgets.collection import CollectionWidget
from desktop.widgets.search import SearchWidget
from desktop.widgets.scan import ScanWidget
from desktop.widgets.containers import ContainersWidget
from desktop.widgets.stats import StatsWidget
from desktop.widgets.settings import SettingsWidget
from desktop.widgets.deck import DeckWidget
from desktop.widgets.overcount import OvercountWidget

_SIDEBAR_WIDTH = 160

_NAV_ITEMS = [
    ("Collection",  "collection"),
    ("Search",      "search"),
    ("Add Card",    "add_card"),
    ("Scanner",     "scan"),
    ("Containers",  "containers"),
    ("Statistics",  "stats"),
    ("Deck Builder","deck"),
    ("Overcount",   "overcount"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MTG Collection Manager")
        self.setMinimumSize(1100, 700)
        self._db_initialized = False
        self._build_ui()
        QTimer.singleShot(0, self._init_db)

    # ------------------------------------------------------------------ #
    # UI                                                                    #
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Sidebar ----
        sidebar = QWidget()
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        sidebar.setObjectName("sidebar")
        sidebar.setStyleSheet(
            "#sidebar { background: #1a1a2e; }"
            "#sidebar QPushButton {"
            "  text-align: left; padding: 10px 16px;"
            "  border: none; color: #cccccc; font-size: 13px;"
            "  background: transparent;"
            "}"
            "#sidebar QPushButton:hover { background: #16213e; color: #ffffff; }"
            "#sidebar QPushButton[active=true] {"
            "  background: #0f3460; color: #ffffff; font-weight: bold;"
            "}"
        )

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 16, 0, 16)
        sidebar_layout.setSpacing(2)

        title = QLabel("MTG\nCollection")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #e94560; font-size: 16px; font-weight: bold; padding: 8px;")
        sidebar_layout.addWidget(title)
        sidebar_layout.addSpacing(8)

        self._nav_buttons: dict[str, QPushButton] = {}
        for label, key in _NAV_ITEMS:
            btn = QPushButton(label)
            btn.setProperty("active", False)
            btn.clicked.connect(lambda checked, k=key: self._navigate(k))
            sidebar_layout.addWidget(btn)
            self._nav_buttons[key] = btn

        sidebar_layout.addStretch()

        # Settings button — pinned to the bottom
        settings_btn = QPushButton("⚙  Settings")
        settings_btn.setProperty("active", False)
        settings_btn.clicked.connect(lambda: self._navigate("settings"))
        sidebar_layout.addWidget(settings_btn)
        self._nav_buttons["settings"] = settings_btn

        # DB path indicator
        from desktop.db import db
        db_lbl = QLabel(f"DB: {db.path}")
        db_lbl.setWordWrap(True)
        db_lbl.setStyleSheet("color: #555; font-size: 9px; padding: 4px 8px;")
        sidebar_layout.addWidget(db_lbl)

        root.addWidget(sidebar)

        # ---- Stacked pages ----
        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        self._pages: dict[str, QWidget] = {}
        for _label, key in _NAV_ITEMS:
            widget = self._create_page(key)
            self._pages[key] = widget
            self._stack.addWidget(widget)

        settings_widget = self._create_page("settings")
        self._pages["settings"] = settings_widget
        self._stack.addWidget(settings_widget)

        self._navigate("collection")

    def _create_page(self, key: str) -> QWidget:
        if key == "collection":
            return CollectionWidget()
        if key == "search":
            return SearchWidget()
        if key == "add_card":
            return AddCardWidget()
        if key == "containers":
            return ContainersWidget()
        if key == "stats":
            return StatsWidget()
        if key == "scan":
            return ScanWidget()
        if key == "settings":
            return SettingsWidget()
        if key == "deck":
            return DeckWidget()
        if key == "overcount":
            return OvercountWidget()
        return QWidget()

    # ------------------------------------------------------------------ #
    # Navigation                                                            #
    # ------------------------------------------------------------------ #

    def _navigate(self, key: str):
        for k, btn in self._nav_buttons.items():
            btn.setProperty("active", k == key)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        page = self._pages.get(key)
        if page:
            self._stack.setCurrentWidget(page)
            if self._db_initialized and hasattr(page, "refresh"):
                page.refresh()

    # ------------------------------------------------------------------ #
    # Window lifecycle                                                       #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        settings = self._pages.get("settings")
        if settings and hasattr(settings, "bot_stop_for_close"):
            if settings.bot_stop_for_close():
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.information(
                    self, "Discord Bot",
                    "The Discord bot has been stopped."
                )
        event.accept()

    # ------------------------------------------------------------------ #
    # DB initialisation                                                     #
    # ------------------------------------------------------------------ #

    def _init_db(self):
        self._do_init_db()

    @asyncSlot()
    async def _do_init_db(self):
        from desktop.db import db

        try:
            await db.initialize()
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self, "Database error",
                f"Failed to initialize database:\n{exc}"
            )
            return

        # Notify all widgets that the DB is ready
        self._db_initialized = True
        for widget in self._pages.values():
            if hasattr(widget, "db_ready"):
                widget.db_ready()

        # Record today's prices in the background — INSERT OR IGNORE so it's
        # a no-op if already run today.
        try:
            await db.record_prices()
        except Exception:
            pass
