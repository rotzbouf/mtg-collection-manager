"""Logs tab — live in-app log viewer backed by a Qt logging handler."""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QPlainTextEdit, QComboBox, QCheckBox, QLabel,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont

from core.i18n import _

logger = logging.getLogger(__name__)

_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG:    "#888888",
    logging.INFO:     "#d4d4d4",
    logging.WARNING:  "#e8c96a",
    logging.ERROR:    "#e07070",
    logging.CRITICAL: "#ff4444",
}

_FONT_FAMILY = "Monospace"
_MAX_LINES   = 2000  # keep the widget from growing unbounded


class _QtLogEmitter(QObject):
    """Minimal QObject whose only job is to carry a pyqtSignal."""
    log_record = pyqtSignal(int, str)  # (levelno, formatted_message)


class QtLogHandler(logging.Handler):
    """Logging handler that emits log records as Qt signals.

    Thread-safe: Qt's signal-slot mechanism queues cross-thread calls
    automatically, so it is safe to emit from any thread.
    """

    def __init__(self, level: int = logging.DEBUG):
        super().__init__(level)
        self._emitter = _QtLogEmitter()
        self.log_record = self._emitter.log_record

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._emitter.log_record.emit(record.levelno, msg)
        except Exception:
            self.handleError(record)


class LogsWidget(QWidget):
    """Read-only log viewer with level filter and auto-scroll."""

    def __init__(self, handler: Optional[QtLogHandler] = None, parent=None):
        super().__init__(parent)
        self._min_level = logging.DEBUG
        self._build_ui()
        if handler:
            self.attach(handler)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Toolbar ──────────────────────────────────────────────────────────
        bar = QHBoxLayout()

        bar.addWidget(QLabel(_("Level:")))
        self._level_combo = QComboBox()
        for name, level in (
            ("DEBUG", logging.DEBUG),
            ("INFO",  logging.INFO),
            ("WARNING", logging.WARNING),
            ("ERROR", logging.ERROR),
        ):
            self._level_combo.addItem(name, level)
        self._level_combo.setCurrentIndex(1)  # INFO default
        self._min_level = logging.INFO
        self._level_combo.currentIndexChanged.connect(self._on_level_change)
        bar.addWidget(self._level_combo)

        bar.addStretch()

        self._autoscroll = QCheckBox(_("Auto-scroll"))
        self._autoscroll.setChecked(True)
        bar.addWidget(self._autoscroll)

        clear_btn = QPushButton(_("Clear"))
        clear_btn.setFixedWidth(60)
        clear_btn.clicked.connect(self._on_clear)
        bar.addWidget(clear_btn)

        root.addLayout(bar)

        # ── Log output ───────────────────────────────────────────────────────
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        font = QFont(_FONT_FAMILY, 9)
        self._text.setFont(font)
        self._text.setMaximumBlockCount(_MAX_LINES)
        self._text.setStyleSheet("background:#1e1e1e; color:#d4d4d4; border:none;")
        root.addWidget(self._text)

    def attach(self, handler: QtLogHandler) -> None:
        """Connect handler signals to this widget."""
        handler.log_record.connect(self._on_record)

    def _on_record(self, levelno: int, message: str) -> None:
        if levelno < self._min_level:
            return

        color = _LEVEL_COLORS.get(levelno, "#d4d4d4")
        cursor = self._text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(message + "\n")

        if self._autoscroll.isChecked():
            self._text.setTextCursor(cursor)
            self._text.ensureCursorVisible()

    def _on_level_change(self, _index: int) -> None:
        self._min_level = self._level_combo.currentData()

    def _on_clear(self) -> None:
        self._text.clear()
