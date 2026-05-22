"""Main application window."""
from __future__ import annotations

import asyncio
import logging

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QLabel, QSizePolicy, QTabWidget,
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
from desktop.widgets.deck_analysis import DeckAnalysisWidget
from desktop.widgets.overcount import OvercountWidget
from desktop.widgets.format_bans import FormatBansWidget
from desktop.widgets.buylists import BuylistsWidget
from desktop.widgets.logs_page import LogsWidget, QtLogHandler

logger = logging.getLogger(__name__)

_SIDEBAR_WIDTH = 160

_NAV_ITEMS = [
    ("Add / Scan",  "add_scan"),
    ("Collection",  "collection"),
    ("Decks",       "decks"),
    ("Search",      "search"),
    ("Buylists",    "buylists"),
    ("Statistics",  "stats"),
    ("Logs",        "logs"),
]


class _TabbedPage(QWidget):
    """Thin wrapper that hosts multiple widgets as sub-tabs and forwards
    db_ready / refresh to all children."""

    def __init__(self, tabs: list[tuple[str, QWidget]]):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tab = QTabWidget()
        self._tab.setDocumentMode(True)
        layout.addWidget(self._tab)
        self._children: list[QWidget] = []
        self._db_ready_flag = False
        for label, widget in tabs:
            self._tab.addTab(widget, label)
            self._children.append(widget)
        self._tab.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, _index: int):
        if not self._db_ready_flag:
            return
        current = self._tab.currentWidget()
        if current and hasattr(current, "refresh"):
            current.refresh()

    def db_ready(self):
        self._db_ready_flag = True
        for child in self._children:
            if hasattr(child, "db_ready"):
                child.db_ready()

    def refresh(self):
        current = self._tab.currentWidget()
        if current and hasattr(current, "refresh"):
            current.refresh()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MTG Collection Manager")
        self.setMinimumSize(1100, 700)
        self._db_initialized = False
        self._log_handler = self._setup_log_handler()
        self._build_ui()
        QTimer.singleShot(0, self._init_db)

    def _setup_log_handler(self) -> QtLogHandler:
        handler = QtLogHandler(level=logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
        return handler

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

        # Price sync status
        self._sync_lbl = QLabel("")
        self._sync_lbl.setWordWrap(True)
        self._sync_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sync_lbl.setStyleSheet("color: #888; font-size: 9px; padding: 2px 8px;")
        sidebar_layout.addWidget(self._sync_lbl)

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

        self._navigate("add_scan")

    def _create_page(self, key: str) -> QWidget:
        if key == "collection":
            return _TabbedPage([
                ("Collection", CollectionWidget()),
                ("Containers", ContainersWidget()),
                ("Overcount",  OvercountWidget()),
                ("Bans",       FormatBansWidget()),
            ])
        if key == "search":
            return SearchWidget()
        if key == "add_scan":
            scan = ScanWidget()
            page = _TabbedPage([("Add Card", AddCardWidget()), ("Scanner", scan)])
            from core.desktop_bridge import bridge
            bridge.register_scan_widget(scan)
            bridge.set_navigate_callback(lambda: self._navigate_to_scanner())
            return page
        if key == "buylists":
            return BuylistsWidget()
        if key == "stats":
            return StatsWidget()
        if key == "decks":
            return _TabbedPage([("Deck Builder", DeckWidget()), ("Deck Analysis", DeckAnalysisWidget())])
        if key == "settings":
            return SettingsWidget()
        if key == "logs":
            return LogsWidget(handler=self._log_handler)
        return QWidget()

    # ------------------------------------------------------------------ #
    # Navigation                                                            #
    # ------------------------------------------------------------------ #

    def _navigate_to_scanner(self):
        """Switch to Add/Scan → Scanner sub-tab (called by the desktop bridge)."""
        self._navigate("add_scan")
        page = self._pages.get("add_scan")
        if page and hasattr(page, "_tab"):
            page._tab.setCurrentIndex(1)  # Scanner is tab index 1

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

    async def _refresh_all_prices(self):
        from desktop.db import db, scryfall

        try:
            ids = await db.get_distinct_scryfall_ids()
            priced_today = await db.get_recently_priced_ids()
        except Exception:
            return

        to_refresh = [sid for sid in ids if sid not in priced_today]
        total = len(to_refresh)
        if total == 0:
            logger.info("Price refresh: all %d cards already priced today, skipping", len(ids))
            self._sync_lbl.setText("✓ Prices up to date")
            QTimer.singleShot(4000, lambda: self._sync_lbl.setText(""))
            return

        logger.info("Refreshing prices for %d/%d cards not yet priced today", total, len(ids))
        self._sync_lbl.setText(f"Prices 0 / {total}…")
        updated = 0
        for i, sid in enumerate(to_refresh, 1):
            try:
                data = await scryfall.get_by_id(sid)
                if data:
                    await db.update_card_prices(
                        sid, data.get("price_eur"), data.get("price_usd")
                    )
                    if data.get("legalities"):
                        await db.update_card_legalities(sid, data["legalities"])
                    if data.get("cardmarket_id"):
                        await db.update_card_cm_id(sid, data["cardmarket_id"])
                    updated += 1
            except Exception:
                pass
            if i % 5 == 0 or i == total:
                self._sync_lbl.setText(f"Prices {i} / {total}…")

        try:
            await db.record_prices()
        except Exception:
            pass

        # Rebuild format ban table from fresh legality data
        try:
            self._sync_lbl.setText("Rebuilding ban lists…")
            ban_count = await db.rebuild_format_bans()
            logger.info("Format bans rebuilt: %d entries", ban_count)
        except Exception:
            pass

        self._sync_lbl.setText(f"✓ {updated} prices updated")
        QTimer.singleShot(6000, lambda: self._sync_lbl.setText(""))

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

        # Record today's prices (snapshot) — INSERT OR IGNORE, no-op if run today.
        try:
            await db.record_prices()
        except Exception:
            pass

        # Rebuild ban lists from stored legalities (fast, no network).
        try:
            await db.rebuild_format_bans()
        except Exception:
            pass

        # Refresh all prices + legalities from Scryfall in the background.
        asyncio.ensure_future(self._refresh_all_prices())
