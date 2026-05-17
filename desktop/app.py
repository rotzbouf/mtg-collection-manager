"""Entry point for the MTG Collection Manager desktop app.

Run with:
    python3 -m desktop.app
"""
from __future__ import annotations

import asyncio
import sys
import os

# Ensure the project root is on the path so ``core`` is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

# qasync must be imported BEFORE the event loop is created.
import qasync
from qasync import QEventLoop

from desktop.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MTG Collection Manager")
    app.setOrganizationName("MTGBot")

    # Apply a dark palette
    _apply_dark_style(app)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = MainWindow()
    window.show()

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
            selection-background-color: #313244;
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
            selection-background-color: #313244;
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
