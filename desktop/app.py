"""Entry point for the MTG Collection Manager desktop app.

Run with:
    python3 -m desktop.app
"""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys

# Ensure the project root is on the path so ``core`` is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

# qasync must be imported BEFORE the event loop is created.
import qasync
from qasync import QEventLoop

from desktop.main_window import MainWindow


async def _shutdown(loop: QEventLoop) -> None:
    """Cancel pending tasks, close resources, then stop the loop."""
    current = asyncio.current_task()

    # 1. Cancel every outstanding coroutine task.
    tasks = [t for t in asyncio.all_tasks() if t is not current]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # 2. Close DB connection and HTTP session so their background threads exit
    #    cleanly before we drain the executor pool.
    try:
        from desktop.db import db, scryfall
        await db.close()
        await scryfall.close()
    except Exception:
        pass

    # 3. Drain asyncio.to_thread pool with a short timeout.
    try:
        await asyncio.wait_for(loop.shutdown_default_executor(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    loop.stop()


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # QtLogHandler in the UI needs DEBUG
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    os.makedirs("logs", exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        "logs/mtg_desktop.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.INFO)  # keep the file readable — no aiosqlite internals
    fh.setFormatter(fmt)
    root.addHandler(fh)


def _run_bot_mode() -> None:
    """Run the Discord bot — used as a subprocess when launched from the bundled exe."""
    import runpy
    runpy.run_module('server.bot', run_name='__main__', alter_sys=True)


def main():
    if '--run-bot' in sys.argv:
        _run_bot_mode()
        return

    _configure_logging()

    import core.config as _cfg
    import core.i18n as _i18n
    _i18n.setup(_cfg.load().get("app", {}).get("language", "en"))

    app = QApplication(sys.argv)
    app.setApplicationName("MTG Collection Manager")
    app.setOrganizationName("MTGBot")

    # App icon — resolved relative to this file so it works both from source and
    # from a Nuitka onefile bundle (which unpacks into a temp dir at runtime).
    _icon_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images", "app_icon.png")
    if os.path.exists(_icon_path):
        app.setWindowIcon(QIcon(_icon_path))

    # Apply a dark palette
    _apply_dark_style(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    app.lastWindowClosed.connect(lambda: loop.create_task(_shutdown(loop)))

    with loop:
        loop.run_forever()

    # PyTorch / EasyOCR create non-daemon threads that Python's normal shutdown
    # would try to join — causing the visible hang on close.  os._exit skips
    # that join sequence and terminates immediately (exit code 0 = clean exit).
    os._exit(0)


def _apply_dark_style(app: QApplication):
    """Apply a simple dark stylesheet to the whole application."""
    app.setStyle("Fusion")
    app.setStyleSheet(
        """
        QWidget {
            background-color: #1e1e2e;
            color: #cdd6f4;
        }
        QMainWindow, QDialog {
            background-color: #1e1e2e;
        }
        QTableWidget {
            background-color: #181825;
            alternate-background-color: #1e1e2e;
            gridline-color: #313244;
            selection-background-color: #3b82c4;
            selection-color: #ffffff;
            color: #cdd6f4;
        }
        QTableWidget QHeaderView::section {
            background-color: #313244;
            color: #cdd6f4;
            padding: 4px;
            border: none;
        }
        QHeaderView::section {
            background-color: #313244;
            color: #cdd6f4;
        }
        QListWidget {
            background-color: #181825;
            alternate-background-color: #1e1e2e;
            selection-background-color: #3b82c4;
            selection-color: #ffffff;
            color: #cdd6f4;
        }
        QLineEdit, QComboBox, QTextEdit {
            background-color: #313244;
            color: #cdd6f4;
            border: 1px solid #45475a;
            border-radius: 4px;
            padding: 4px 6px;
        }
        QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
            border: 1px solid #89b4fa;
        }
        QPushButton {
            background-color: #313244;
            color: #cdd6f4;
            border: 1px solid #45475a;
            border-radius: 4px;
            padding: 5px 12px;
        }
        QPushButton:hover {
            background-color: #45475a;
        }
        QPushButton:pressed {
            background-color: #585b70;
        }
        QPushButton:disabled {
            color: #585b70;
        }
        QScrollArea {
            border: none;
        }
        QSplitter::handle {
            background-color: #313244;
        }
        QProgressBar {
            background-color: #313244;
            border: 1px solid #45475a;
            border-radius: 4px;
            text-align: center;
            color: #cdd6f4;
        }
        QProgressBar::chunk {
            background-color: #89b4fa;
            border-radius: 3px;
        }
        QScrollBar:vertical {
            background: #1e1e2e;
            width: 10px;
        }
        QScrollBar::handle:vertical {
            background: #45475a;
            border-radius: 5px;
            min-height: 20px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar:horizontal {
            background: #1e1e2e;
            height: 10px;
        }
        QScrollBar::handle:horizontal {
            background: #45475a;
            border-radius: 5px;
            min-width: 20px;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        QMessageBox {
            background-color: #1e1e2e;
            color: #cdd6f4;
        }
        QLabel {
            color: #cdd6f4;
        }
        QCheckBox {
            color: #cdd6f4;
        }
        QGroupBox {
            color: #cdd6f4;
            border: 1px solid #45475a;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 8px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
        }
        QComboBox QAbstractItemView {
            background-color: #313244;
            color: #cdd6f4;
            selection-background-color: #45475a;
        }
        """
    )


if __name__ == "__main__":
    main()
