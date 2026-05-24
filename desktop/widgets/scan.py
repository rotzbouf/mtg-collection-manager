"""Scan tab — OCR card scanner (EasyOCR / tesseract + OpenCV)."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QComboBox, QLineEdit,
    QFrame, QSizePolicy, QMessageBox, QFileDialog,
    QApplication,
)
from PyQt6.QtCore import Qt, QTimer, QMimeData, pyqtSignal
from PyQt6.QtGui import QPixmap, QDragEnterEvent, QDropEvent, QImage, QKeySequence
from qasync import asyncSlot

from desktop.utils import display_name, lang_flag, format_price
from desktop.widgets.card_detail import CardDetailPanel

logger = logging.getLogger(__name__)

_ACCEPTED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".heic"}

_OCR_STATUS_STYLE = "font-size: 12px; color: #888; padding: 2px 0;"
_MATCH_OK_STYLE   = "font-size: 12px; color: #7ec8a0; padding: 2px 0;"
_MATCH_FAIL_STYLE = "font-size: 12px; color: #e07070; padding: 2px 0;"


# ── Drop zone ─────────────────────────────────────────────────────────────────

class _DropZone(QLabel):
    """An image drop/paste area that emits image_dropped(bytes) on activation."""

    image_dropped = pyqtSignal(bytes)

    _IDLE_STYLE = (
        "background: #1a1a2e; border: 2px dashed #444; border-radius: 10px;"
        "color: #666; font-size: 13px;"
    )
    _HOVER_STYLE = (
        "background: #1e2a3e; border: 2px dashed #5588cc; border-radius: 10px;"
        "color: #99bbdd; font-size: 13px;"
    )
    _BUSY_STYLE = (
        "background: #1a1a2e; border: 2px solid #555; border-radius: 10px;"
        "color: #aaa; font-size: 13px;"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setMinimumSize(300, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._reset()

    def _reset(self):
        self.setText("Drop card image here\nor click Browse / press Ctrl+V")
        self.setStyleSheet(self._IDLE_STYLE)
        self.setPixmap(QPixmap())

    def show_preview(self, image_bytes: bytes):
        """Display the isolated-card preview image."""
        img = QImage.fromData(image_bytes)
        if img.isNull():
            return
        pm = QPixmap.fromImage(img)
        pm = pm.scaled(
            self.width() - 16, self.height() - 16,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setText("")
        self.setPixmap(pm)
        self.setStyleSheet(self._BUSY_STYLE)

    def set_busy(self, text: str = "Scanning…"):
        self.setText(text)
        self.setPixmap(QPixmap())
        self.setStyleSheet(self._BUSY_STYLE)

    # ── drag-and-drop ──────────────────────────────────────────────────────────

    def dragEnterEvent(self, event: QDragEnterEvent):
        if self._has_image(event.mimeData()):
            event.acceptProposedAction()
            self.setStyleSheet(self._HOVER_STYLE)
        else:
            event.ignore()

    def dragLeaveEvent(self, _event):
        self.setStyleSheet(self._IDLE_STYLE)

    def dropEvent(self, event: QDropEvent):
        self.setStyleSheet(self._IDLE_STYLE)
        data = event.mimeData()
        if data.hasUrls():
            for url in data.urls():
                path = url.toLocalFile()
                if any(path.lower().endswith(ext) for ext in _ACCEPTED_EXTS):
                    try:
                        with open(path, "rb") as fh:
                            self.image_dropped.emit(fh.read())
                    except OSError as exc:
                        logger.warning("Could not read dropped file: %s", exc)
                    break
        elif data.hasImage():
            img: QImage = data.imageData()
            if not img.isNull():
                buf = io.BytesIO()
                img.save(
                    buf.getbuffer() if hasattr(buf, "getbuffer") else buf,
                    format="PNG",
                )
                # QImage.save to bytes via QByteArray
                from PyQt6.QtCore import QByteArray, QBuffer
                ba = QByteArray()
                qbuf = QBuffer(ba)
                qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
                img.save(qbuf, "PNG")
                self.image_dropped.emit(bytes(ba))

    @staticmethod
    def _has_image(mime: QMimeData) -> bool:
        if mime.hasImage():
            return True
        if mime.hasUrls():
            return any(
                url.toLocalFile().lower().endswith(tuple(_ACCEPTED_EXTS))
                for url in mime.urls()
            )
        return False


# ── Main widget ───────────────────────────────────────────────────────────────

class ScanWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._containers: list[dict] = []
        self._pending_card: Optional[dict] = None
        self._last_container_id: Optional[int] = None
        self._ocr_initialised = False
        self._discord_future: Optional[asyncio.Future] = None
        self._build_ui()

    def db_ready(self):
        QTimer.singleShot(0, self._init_load)

    def refresh(self):
        self._load_containers()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left pane -------------------------------------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        # Header + Browse button
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("<h2>Card Scanner</h2>"))
        header_row.addStretch()
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setToolTip("Open an image file")
        header_row.addWidget(self._browse_btn)
        left_layout.addLayout(header_row)

        # Discord remote scan banner (hidden until a Discord scan arrives)
        self._discord_banner = QLabel("")
        self._discord_banner.setWordWrap(True)
        self._discord_banner.setStyleSheet(
            "background: #0f3460; color: #ffffff; padding: 6px 10px;"
            "font-size: 12px; border-radius: 4px;"
        )
        self._discord_banner.setVisible(False)
        left_layout.addWidget(self._discord_banner)

        # OCR availability notice (shown if neither engine is installed)
        self._ocr_warn = QLabel(
            "⚠ No OCR engine installed.  Install easyocr or pytesseract to enable scanning."
        )
        self._ocr_warn.setWordWrap(True)
        self._ocr_warn.setStyleSheet("color: #e07070; font-size: 12px; padding: 4px;")
        self._ocr_warn.setVisible(False)
        left_layout.addWidget(self._ocr_warn)

        # EasyOCR init notice
        self._ocr_init_lbl = QLabel("⏳ Loading OCR engine…")
        self._ocr_init_lbl.setStyleSheet("color: #aaa; font-size: 12px;")
        self._ocr_init_lbl.setVisible(False)
        left_layout.addWidget(self._ocr_init_lbl)

        # Drop zone
        self._drop_zone = _DropZone()
        left_layout.addWidget(self._drop_zone, stretch=1)

        # OCR status labels
        self._status_collector = QLabel("")
        self._status_collector.setStyleSheet(_OCR_STATUS_STYLE)
        self._status_collector.setWordWrap(True)
        self._status_name = QLabel("")
        self._status_name.setStyleSheet(_OCR_STATUS_STYLE)
        self._status_name.setWordWrap(True)
        self._status_match = QLabel("")
        self._status_match.setStyleSheet(_OCR_STATUS_STYLE)
        self._status_match.setWordWrap(True)
        left_layout.addWidget(self._status_collector)
        left_layout.addWidget(self._status_name)
        left_layout.addWidget(self._status_match)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        left_layout.addWidget(sep)

        # Language selector (auto-populated from scan; can be overridden)
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self._lang_cb = QComboBox()
        from desktop.widgets.add_card import _LANGUAGES
        for code, label in _LANGUAGES:
            self._lang_cb.addItem(label, code)
        self._lang_cb.setToolTip(
            "Auto-detected from scan — override if the detected language is wrong"
        )
        lang_row.addWidget(self._lang_cb, stretch=1)
        left_layout.addLayout(lang_row)

        # Container selector
        cont_row = QHBoxLayout()
        cont_row.addWidget(QLabel("Container:"))
        self._container_cb = QComboBox()
        self._container_cb.setMinimumWidth(180)
        cont_row.addWidget(self._container_cb, stretch=1)
        left_layout.addLayout(cont_row)

        # Action buttons
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("✅ Add")
        self._add_btn.setEnabled(False)
        self._foil_btn = QPushButton("✨ Add as foil")
        self._foil_btn.setEnabled(False)
        self._skip_btn = QPushButton("✖ Skip")
        self._skip_btn.setEnabled(False)
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._foil_btn)
        btn_row.addWidget(self._skip_btn)
        left_layout.addLayout(btn_row)

        # Manual name correction (hidden until needed)
        self._manual_frame = QWidget()
        manual_layout = QHBoxLayout(self._manual_frame)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        manual_layout.setSpacing(4)
        self._manual_edit = QLineEdit()
        self._manual_edit.setPlaceholderText("Correct card name…")
        self._manual_search_btn = QPushButton("Search")
        manual_layout.addWidget(QLabel("Name:"))
        manual_layout.addWidget(self._manual_edit, stretch=1)
        manual_layout.addWidget(self._manual_search_btn)
        self._manual_frame.setVisible(False)
        left_layout.addWidget(self._manual_frame)

        splitter.addWidget(left)

        # ---- Right pane — card detail ----------------------------------------
        self._detail = CardDetailPanel(show_buttons=False)
        self._detail.setMinimumWidth(340)
        self._detail.setMaximumWidth(460)
        splitter.addWidget(self._detail)

        splitter.setSizes([480, 400])
        root.addWidget(splitter)

        # ── Signals ────────────────────────────────────────────────────────────
        self._drop_zone.image_dropped.connect(self._on_image)
        self._browse_btn.clicked.connect(self._on_browse)
        self._add_btn.clicked.connect(lambda: self._on_confirm(foil=False))
        self._foil_btn.clicked.connect(lambda: self._on_confirm(foil=True))
        self._skip_btn.clicked.connect(self._on_skip)
        self._manual_search_btn.clicked.connect(self._on_manual_search)
        self._manual_edit.returnPressed.connect(self._on_manual_search)

    # ── Paste support (Ctrl+V) ─────────────────────────────────────────────────

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste_from_clipboard()
        else:
            super().keyPressEvent(event)

    def _paste_from_clipboard(self):
        cb = QApplication.clipboard()
        mime = cb.mimeData()

        if mime.hasImage():
            img: QImage = cb.image()
            if not img.isNull():
                from PyQt6.QtCore import QByteArray, QBuffer
                ba = QByteArray()
                qbuf = QBuffer(ba)
                qbuf.open(QBuffer.OpenModeFlag.WriteOnly)
                img.save(qbuf, "PNG")
                self._on_image(bytes(ba))
                return

        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if any(path.lower().endswith(ext) for ext in _ACCEPTED_EXTS):
                    try:
                        with open(path, "rb") as fh:
                            self._on_image(fh.read())
                    except OSError as exc:
                        logger.warning("Could not read pasted file: %s", exc)
                    break

    # ── Init ──────────────────────────────────────────────────────────────────

    @asyncSlot()
    async def _init_load(self):
        await self._load_containers()
        await self._init_ocr()

    @asyncSlot()
    async def _load_containers(self):
        from desktop.db import db

        self._containers = await db.list_containers()
        self._container_cb.blockSignals(True)
        prev_id = (
            self._container_cb.currentData()
            if self._container_cb.count() > 0
            else self._last_container_id
        )
        self._container_cb.clear()
        self._container_cb.addItem("(no container)", None)
        for c in self._containers:
            self._container_cb.addItem(f"{c['name']}  ({c.get('type', '')})", c["id"])
        # Restore previous selection
        if prev_id is not None:
            for i in range(self._container_cb.count()):
                if self._container_cb.itemData(i) == prev_id:
                    self._container_cb.setCurrentIndex(i)
                    break
        self._container_cb.blockSignals(False)

        from core import scanner as sc
        if not sc.ocr_available():
            self._ocr_warn.setVisible(True)
            self._browse_btn.setEnabled(False)
            self._drop_zone.setEnabled(False)

    @asyncSlot()
    async def _init_ocr(self):
        from core import scanner as sc

        if self._ocr_initialised or not sc.ocr_available():
            return

        self._ocr_init_lbl.setVisible(True)
        try:
            await sc.init_ocr()
        except Exception as exc:
            logger.warning("OCR init failed: %s", exc)
        finally:
            self._ocr_init_lbl.setVisible(False)
            self._ocr_initialised = True

    # ── File picker ────────────────────────────────────────────────────────────

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open card image",
            "", "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.tiff *.heic)"
        )
        if path:
            try:
                with open(path, "rb") as fh:
                    self._on_image(fh.read())
            except OSError as exc:
                QMessageBox.warning(self, "File error", str(exc))

    # ── Scan pipeline ──────────────────────────────────────────────────────────

    def _on_image(self, image_bytes: bytes):
        from core.scanner import MAX_INPUT_BYTES
        if len(image_bytes) > MAX_INPUT_BYTES:
            QMessageBox.warning(
                self, "Image too large",
                f"Maximum scan image size is {MAX_INPUT_BYTES // (1024 * 1024)} MB.",
            )
            return
        self._pending_card = None
        self._clear_result()
        self._drop_zone.set_busy("Scanning…")
        self._run_scan(image_bytes)

    @asyncSlot()
    async def _run_scan(self, image_bytes: bytes):
        from core import scanner as sc
        from core.scan_service import resolve_scan, no_match_message
        from desktop.db import scryfall

        try:
            preview = await asyncio.to_thread(sc.get_isolated_preview, image_bytes)
            if preview:
                self._drop_zone.show_preview(preview)
        except Exception:
            pass

        self._status_match.setText("🔍 Scanning…")
        self._status_match.setStyleSheet(_OCR_STATUS_STYLE)

        try:
            card, detected_lang, method_parts, extracted_name, collector_info = (
                await resolve_scan(scryfall, image_bytes)
            )
        except Exception as exc:
            self._status_match.setText(f"Scan error: {exc}")
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            self._show_manual_input()
            return

        if collector_info.get("set_code") or collector_info.get("collector_number"):
            sc_info = collector_info.get("set_code", "—")
            cn_info = collector_info.get("collector_number", "—")
            lang_info = collector_info.get("language", "—")
            self._status_collector.setText(f"📋 Collector: {sc_info} #{cn_info}  lang: {lang_info}")
        else:
            self._status_collector.setText("📋 Collector: —")

        if extracted_name:
            self._status_name.setText(f'🔠 Name OCR: "{extracted_name}"')
        else:
            self._status_name.setText("🔠 Name OCR: —")

        if card is None:
            msg = no_match_message(extracted_name, collector_info)
            self._status_match.setText(f"❌ {msg}")
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            self._show_manual_input()
            return

        card["language"] = detected_lang or "en"
        self._set_lang_cb(card["language"])
        method_str = "  •  ".join(method_parts)
        self._status_match.setText(f"✓ {method_str}")
        self._status_match.setStyleSheet(_MATCH_OK_STYLE)
        self._show_result(card)

    # ── Manual name search ─────────────────────────────────────────────────────

    def _on_manual_search(self):
        name = self._manual_edit.text().strip()
        if not name:
            return
        self._do_manual_search(name)

    @asyncSlot()
    async def _do_manual_search(self, name: str):
        from desktop.db import scryfall

        self._manual_search_btn.setEnabled(False)
        self._status_match.setText("🌐 Searching Scryfall…")
        self._status_match.setStyleSheet(_OCR_STATUS_STYLE)

        try:
            card, detected_lang = await scryfall.resolve_card(name)
        except Exception as exc:
            self._status_match.setText(f"Error: {exc}")
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            self._manual_search_btn.setEnabled(True)
            return

        self._manual_search_btn.setEnabled(True)

        if card is None:
            self._status_match.setText(f'❌ "{name}" not found on Scryfall.')
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            return

        card["language"] = detected_lang or "en"
        self._set_lang_cb(card["language"])
        self._status_match.setText(f'✓ Found via manual search: "{name}"')
        self._status_match.setStyleSheet(_MATCH_OK_STYLE)
        self._show_result(card)

    # ── Result display ─────────────────────────────────────────────────────────

    def _show_result(self, card: dict):
        self._pending_card = card
        self._detail.set_card(card)
        self._add_btn.setEnabled(True)
        self._foil_btn.setEnabled(True)
        self._skip_btn.setEnabled(True)
        self._manual_frame.setVisible(False)

    def _clear_result(self):
        self._pending_card = None
        self._detail.clear()
        self._add_btn.setEnabled(False)
        self._foil_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._status_collector.setText("")
        self._status_name.setText("")
        self._status_match.setText("")
        self._manual_frame.setVisible(False)
        self._manual_edit.clear()

    def _show_manual_input(self):
        self._manual_frame.setVisible(True)
        self._skip_btn.setEnabled(True)
        self._manual_edit.setFocus()

    # ── Confirm / skip ─────────────────────────────────────────────────────────

    def _on_confirm(self, foil: bool):
        if self._pending_card is None:
            return
        container_id = self._container_cb.currentData()
        self._last_container_id = container_id
        self._do_save(self._pending_card, foil, container_id)

    def _set_lang_cb(self, lang: str) -> None:
        """Set the language combobox to the given language code."""
        idx = self._lang_cb.findData(lang)
        if idx >= 0:
            self._lang_cb.setCurrentIndex(idx)

    @asyncSlot()
    async def _do_save(self, card: dict, foil: bool, container_id: Optional[int]):
        from desktop.db import db

        card = dict(card)
        card["foil"] = foil
        card["condition"] = card.get("condition") or "NM"
        card["quantity"] = 1
        card["container_id"] = container_id
        # Always use the (possibly user-corrected) language combobox value so
        # the stored language matches what the user confirmed, even when the
        # Scryfall lookup fell back from the detected language to English.
        card["language"] = self._lang_cb.currentData() or card.get("language") or "en"

        self._add_btn.setEnabled(False)
        self._foil_btn.setEnabled(False)

        try:
            row_id = await db.add_card(card, added_by="desktop")
        except Exception as exc:
            QMessageBox.critical(self, "Save error", str(exc))
            self._add_btn.setEnabled(True)
            self._foil_btn.setEnabled(True)
            return

        name = display_name(card)
        foil_tag = " ✨" if foil else ""
        self._status_match.setText(
            f"✅ Saved: {name}{foil_tag}  (ID {row_id})"
        )
        self._status_match.setStyleSheet(_MATCH_OK_STYLE)

        # Resolve pending Discord future so the bot can post confirmation
        container_name = next(
            (c["name"] for c in self._containers if c["id"] == container_id),
            None,
        )
        self._resolve_discord_future({
            "status": "added",
            "card": card,
            "row_id": row_id,
            "foil": foil,
            "container_id": container_id,
            "container_name": container_name,
        })

        # Reset for the next card, keep container and drop zone preview
        self._pending_card = None
        self._add_btn.setEnabled(False)
        self._foil_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._manual_frame.setVisible(False)
        self._detail.clear()
        QTimer.singleShot(1500, self._reset_for_next)

    def _reset_for_next(self):
        self._drop_zone._reset()
        self._status_collector.setText("")
        self._status_name.setText("")
        self._status_match.setText("")
        self._manual_edit.clear()

    def _on_skip(self):
        self._resolve_discord_future({"status": "skipped"})
        self._drop_zone._reset()
        self._clear_result()

    # ── Discord bridge ─────────────────────────────────────────────────────────

    def inject_discord_scan(self, image_bytes: bytes, discord_user: str) -> asyncio.Future:
        """Called by the desktop bridge to route a Discord scan through the desktop UI."""
        if self._discord_future and not self._discord_future.done():
            # Already handling one — reject
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            fut.set_result({"status": "error", "message": "Scanner is busy"})
            return fut

        self._discord_future = asyncio.get_running_loop().create_future()
        self._discord_banner.setText(
            f"📱 Discord scan from {discord_user} — confirm to add, Skip to decline"
        )
        self._discord_banner.setVisible(True)
        self.window().raise_()
        self.window().activateWindow()
        self._on_image(image_bytes)
        return self._discord_future

    def cancel_discord_scan(self) -> None:
        self._resolve_discord_future({"status": "skipped", "reason": "timeout"})
        self._discord_banner.setVisible(False)
        self._on_skip()

    def _resolve_discord_future(self, result: dict) -> None:
        fut = self._discord_future
        if fut and not fut.done():
            fut.set_result(result)
        self._discord_future = None
        self._discord_banner.setVisible(False)


