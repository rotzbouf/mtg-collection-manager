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

from PyQt6.QtWidgets import QApplication

# qasync must be imported BEFORE the event loop is created.
import qasync
from qasync import QEventLoop

from desktop.main_window import MainWindow


async def _shutdown(loop: QEventLoop) -> None:
    """Cancel pending tasks, flush the thread-pool with a timeout, then stop."""
    current = asyncio.current_task()

    # 1. Cancel every outstanding coroutine task.
    tasks = [t for t in asyncio.all_tasks() if t is not current]
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    # 2. Wait for thread-pool threads (asyncio.to_thread) to drain, but give
    #    them at most 2 s — image loads or DB calls that are mid-flight are
    #    abandoned rather than keeping the process alive indefinitely.
    try:
        await asyncio.wait_for(loop.shutdown_default_executor(), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    loop.stop()


def _configure_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    os.makedirs("logs", exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        "logs/mtg_desktop.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


def main():
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("MTG Collection Manager")
    app.setOrganizationName("MTGBot")

    # Apply a dark palette
    _apply_dark_style(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

    app.lastWindowClosed.connect(lambda: loop.create_task(_shutdown(loop)))

    with loop:
        loop.run_forever()


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
