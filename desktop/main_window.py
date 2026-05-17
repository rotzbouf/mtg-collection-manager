"""Main application window."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QStackedWidget, QLabel, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer
from qasync import asyncSlot

from desktop.widgets.collection import CollectionWidget
from desktop.widgets.containers import ContainersWidget
from desktop.widgets.stats import StatsWidget
from desktop.widgets.import_export import ImportExportWidget
from desktop.widgets.deck import DeckWidget

_SIDEBAR_WIDTH = 160

_NAV_ITEMS = [
    ("Collection",     "collection"),
    ("Containers",     "containers"),
    ("Statistics",     "stats"),
    ("Import / Export","import_export"),
    ("Deck Builder",   "deck"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MTG Collection Manager")
        self.setMinimumSize(1100, 700)
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

        self._navigate("collection")

    def _create_page(self, key: str) -> QWidget:
        if key == "collection":
            return CollectionWidget()
        if key == "containers":
            return ContainersWidget()
        if key == "stats":
            return StatsWidget()
        if key == "import_export":
            return ImportExportWidget()
        if key == "deck":
            return DeckWidget()
        return QWidget()

    # ------------------------------------------------------------------ #
    # Navigation                                                            #
    # ------------------------------------------------------------------ #

    def _navigate(self, key: str):
        for k, btn in self._nav_buttons.items():
            btn.setProperty("active", k == key)
            # Force style refresh
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        page = self._pages.get(key)
        if page:
            self._stack.setCurrentWidget(page)

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
