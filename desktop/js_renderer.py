"""Off-screen JavaScript page renderer using QWebEnginePage.

Falls back gracefully when PyQt6-WebEngine is not installed — callers should
check ``JsRenderer.available()`` before use.

Usage
-----
    html = await JsRenderer.instance().render("https://example.com/buylist")

A single QWebEnginePage is shared across all calls (serialised via an
asyncio Lock) so there is at most one concurrent browser navigation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from PyQt6.QtCore import QUrl, QTimer
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    logger.debug(
        "PyQt6-WebEngine not installed — JS rendering unavailable. "
        "Install with: pip install PyQt6-WebEngine"
    )


class JsRenderer:
    """Singleton off-screen JavaScript renderer.

    The QWebEnginePage is created lazily on first use (after QApplication
    is initialised) and reused for all subsequent requests.
    """

    _instance: "JsRenderer | None" = None

    @classmethod
    def instance(cls) -> "JsRenderer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def available() -> bool:
        """Return True if PyQt6-WebEngine is installed."""
        return _AVAILABLE

    # ── Internal ──────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        self._page: "QWebEnginePage | None" = None
        self._lock: asyncio.Lock | None = None

    def _get_page(self) -> "QWebEnginePage":
        if self._page is None:
            self._page = QWebEnginePage()
            # Silence JavaScript console messages in the log
            self._page.javaScriptConsoleMessage = lambda *_: None  # type: ignore[method-assign]
        return self._page

    def _get_lock(self) -> asyncio.Lock:
        # Lazy — must not be created at import time before the event loop exists
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ── Public API ────────────────────────────────────────────────────────────

    async def render(
        self,
        url: str,
        wait_ms: int = 2500,
        timeout: float = 35.0,
    ) -> Optional[str]:
        """Load *url* with a full browser engine and return the rendered HTML.

        Parameters
        ----------
        url:
            The page to load.
        wait_ms:
            Extra milliseconds to wait after ``loadFinished`` before extracting
            HTML.  Gives JavaScript time to finish populating the DOM.
        timeout:
            Total seconds before giving up.

        Returns
        -------
        str | None
            Fully rendered HTML, or ``None`` on timeout / load error.
        """
        if not _AVAILABLE:
            return None

        async with self._get_lock():
            loop = asyncio.get_event_loop()
            future: asyncio.Future[Optional[str]] = loop.create_future()
            page = self._get_page()

            def _on_load_finished(ok: bool) -> None:
                if not ok:
                    if not future.done():
                        future.set_result(None)
                    return

                def _extract() -> None:
                    def _on_html(html: str) -> None:
                        if not future.done():
                            future.set_result(html if len(html) >= 300 else None)
                    page.toHtml(_on_html)

                QTimer.singleShot(wait_ms, _extract)

            page.loadFinished.connect(_on_load_finished)
            try:
                page.load(QUrl(url))
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning("JS render timeout (%.0fs) for %s", timeout, url)
                return None
            finally:
                try:
                    page.loadFinished.disconnect(_on_load_finished)
                except Exception:
                    pass

    def close(self) -> None:
        """Release the underlying QWebEnginePage.  Call on application shutdown."""
        if self._page is not None:
            self._page.deleteLater()
            self._page = None
            JsRenderer._instance = None
