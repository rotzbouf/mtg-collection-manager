"""Scan tab — OCR card scanner (EasyOCR / tesseract + OpenCV)."""
from __future__ import annotations

import asyncio
import difflib
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
        self._pending_card: Optional[dict] = None  # matched Scryfall card
        self._last_container_id: Optional[int] = None
        self._ocr_initialised = False
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
        self._pending_card = None
        self._clear_result()
        self._drop_zone.set_busy("Scanning…")
        self._run_scan(image_bytes)

    @asyncSlot()
    async def _run_scan(self, image_bytes: bytes):
        from core import scanner as sc
        from desktop.db import scryfall

        # Show isolated preview while OCR runs
        try:
            preview = await asyncio.to_thread(sc.get_isolated_preview, image_bytes)
            if preview:
                self._drop_zone.show_preview(preview)
        except Exception:
            pass

        # Run OCR (name + footer) in threads — EasyOCR is not thread-safe for
        # concurrent calls so we run them sequentially inside a single thread.
        self._status_match.setText("🔍 Running OCR…")
        self._status_match.setStyleSheet(_OCR_STATUS_STYLE)

        try:
            extracted_name, collector_info = await asyncio.to_thread(
                _run_ocr_sync, image_bytes
            )
        except Exception as exc:
            self._status_match.setText(f"OCR error: {exc}")
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            self._show_manual_input()
            return

        # Display OCR raw results
        if collector_info.get("set_code") or collector_info.get("collector_number"):
            sc_info = collector_info.get("set_code", "—")
            cn_info = collector_info.get("collector_number", "—")
            lang_info = collector_info.get("language", "—")
            self._status_collector.setText(
                f"📋 Collector: {sc_info} #{cn_info}  lang: {lang_info}"
            )
        else:
            self._status_collector.setText("📋 Collector: —")

        if extracted_name:
            self._status_name.setText(f'🔠 Name OCR: "{extracted_name}"')
        else:
            self._status_name.setText("🔠 Name OCR: —")

        # Resolve against Scryfall
        self._status_match.setText("🌐 Querying Scryfall…")
        try:
            card, detected_lang, method_parts = await _resolve_scryfall(
                scryfall, extracted_name, collector_info
            )
        except Exception as exc:
            self._status_match.setText(f"Scryfall error: {exc}")
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            self._show_manual_input()
            return

        if card is None:
            msg = _no_match_msg(extracted_name, collector_info)
            self._status_match.setText(f"❌ {msg}")
            self._status_match.setStyleSheet(_MATCH_FAIL_STYLE)
            self._show_manual_input()
            return

        card["language"] = detected_lang or "en"
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

    @asyncSlot()
    async def _do_save(self, card: dict, foil: bool, container_id: Optional[int]):
        from desktop.db import db

        card = dict(card)
        card["foil"] = foil
        card["condition"] = card.get("condition") or "NM"
        card["quantity"] = 1
        card["container_id"] = container_id

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
        self._drop_zone._reset()
        self._clear_result()


# ── Helpers (module-level, called in threads) ─────────────────────────────────

def _run_ocr_sync(image_bytes: bytes) -> tuple[Optional[str], dict]:
    """Run name OCR and footer OCR sequentially in a worker thread."""
    import core.scanner as sc

    extracted_name = sc.extract_name(image_bytes)
    collector_info = sc.extract_collector_info(image_bytes) or {}
    return extracted_name, collector_info


async def _resolve_scryfall(
    scryfall,
    extracted_name: Optional[str],
    collector_info: dict,
) -> tuple[Optional[dict], str, list[str]]:
    """Mirror of cogs/scan._resolve_scan adapted for the desktop singleton."""
    method_parts: list[str] = []

    # Try collector match first (exact set+number lookup)
    collector_card: Optional[dict] = None
    if collector_info.get("set_code") and collector_info.get("collector_number"):
        clang = collector_info.get("language") or "en"
        collector_card = await scryfall.get_by_collector(
            collector_info["set_code"], collector_info["collector_number"], clang
        )
        if not collector_card and clang != "en":
            collector_card = await scryfall.get_by_collector(
                collector_info["set_code"], collector_info["collector_number"], "en"
            )

    # OCR name match if collector lookup failed
    ocr_card: Optional[dict] = None
    ocr_lang = "unknown"
    if extracted_name and not collector_card:
        set_hint = collector_info.get("set_code")
        ocr_card, ocr_lang = await scryfall.resolve_card(extracted_name, set_code=set_hint)
        if not ocr_card and set_hint:
            ocr_card, ocr_lang = await scryfall.resolve_card(extracted_name)

    footer_lang = collector_info.get("language")

    if collector_card:
        detected_lang = footer_lang or "en"
        sc_code = collector_info["set_code"]
        cn_code = collector_info["collector_number"]
        method_parts.append(f"collector [{sc_code} #{cn_code}]")
        if extracted_name:
            en = collector_card.get("name_en", "")
            de = collector_card.get("name_de") or collector_card.get("printed_name", "")
            ratio = max(
                difflib.SequenceMatcher(None, extracted_name.lower(), en.lower()).ratio(),
                difflib.SequenceMatcher(None, extracted_name.lower(), de.lower()).ratio() if de else 0,
            )
            if ratio >= 0.55:
                method_parts.append(f'name confirmed "{extracted_name}" ({ratio:.0%})')
            else:
                method_parts.append(f'OCR name "{extracted_name}" ({ratio:.0%} match)')
        return collector_card, detected_lang, method_parts

    if ocr_card:
        detected_lang = footer_lang or (ocr_lang if ocr_lang != "unknown" else "en")
        method_parts.append(f'OCR [{ocr_lang}] "{extracted_name}"')
        if footer_lang:
            method_parts.append(f"lang {footer_lang} (footer)")
        return ocr_card, detected_lang, method_parts

    return None, "en", []


def _no_match_msg(extracted_name: Optional[str], collector_info: dict) -> str:
    import core.scanner as sc

    if not sc.ocr_available():
        return "No OCR engine installed. Enter the name manually."
    if collector_info.get("set_code") and collector_info.get("collector_number"):
        return (
            f'Collector info found ({collector_info["set_code"]} '
            f'#{collector_info["collector_number"]}) but no Scryfall match.'
        )
    if extracted_name:
        return f'Could not match "{extracted_name}" on Scryfall. Enter the name manually.'
    return "Could not read the card name. Enter it manually below."
