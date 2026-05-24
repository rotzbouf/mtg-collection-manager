"""Desktop bridge — Unix socket server that routes Discord scans to the desktop scanner.

The desktop app starts this server when launching the bot. The bot connects per-scan
(no persistent connection) and sends image bytes; the bridge forwards them to the
ScanWidget, waits for the user to confirm or skip, then returns the result.

Protocol: 4-byte big-endian uint32 length prefix + UTF-8 JSON body, both directions.

Request  (bot → desktop):  {"image_b64": "...", "discord_user": "@name"}
Response (desktop → bot):  {"status": "added", "card": {...}, "row_id": 123, ...}
                          | {"status": "skipped"}
                          | {"status": "error", "message": "..."}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import struct
import weakref
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SOCK_PATH = str(Path(os.getenv("TMPDIR", "/tmp")) / "mtg_collection_bridge.sock")

# How long to wait for the user to confirm/skip in the desktop app
_USER_TIMEOUT = 300


class DesktopBridge:
    def __init__(self):
        self._scan_widget_ref = None
        self._navigate_cb: Optional[Callable] = None
        self._server: Optional[asyncio.AbstractServer] = None

    def register_scan_widget(self, widget) -> None:
        self._scan_widget_ref = weakref.ref(widget)

    def set_navigate_callback(self, cb: Callable) -> None:
        self._navigate_cb = cb

    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> None:
        task = asyncio.ensure_future(self._start_server())
        task.add_done_callback(
            lambda f: logger.error("Bridge startup failed: %s", f.exception())
            if not f.cancelled() and f.exception() else None
        )

    def stop(self) -> None:
        if self._server:
            self._server.close()
            self._server = None
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass
        logger.info("Desktop bridge stopped")

    async def _start_server(self) -> None:
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass
        try:
            self._server = await asyncio.start_unix_server(self._handle_client, SOCK_PATH)
            os.chmod(SOCK_PATH, 0o600)  # restrict to owner only (L-1)
            logger.info("Desktop bridge listening at %s", SOCK_PATH)
        except Exception as exc:
            logger.error("Desktop bridge failed to start: %s", exc)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw_len = await asyncio.wait_for(reader.readexactly(4), timeout=10.0)
            length = struct.unpack(">I", raw_len)[0]
            if length > 20 * 1024 * 1024:
                raise ValueError(f"Request too large: {length}")
            raw = await asyncio.wait_for(reader.readexactly(length), timeout=30.0)
            req = json.loads(raw.decode())

            image_bytes = base64.b64decode(req["image_b64"])
            discord_user = req.get("discord_user", "Discord")

            if self._navigate_cb:
                self._navigate_cb()

            widget = self._scan_widget_ref() if self._scan_widget_ref else None
            if widget is None:
                _send(writer, {"status": "error", "message": "Scanner not available"})
                return

            future = widget.inject_discord_scan(image_bytes, discord_user)
            try:
                result = await asyncio.wait_for(future, timeout=_USER_TIMEOUT)
            except asyncio.TimeoutError:
                result = {"status": "skipped", "reason": "timeout"}
                widget.cancel_discord_scan()

            _send(writer, result)
        except Exception as exc:
            logger.error("Bridge client error: %s", exc, exc_info=True)
            try:
                _send(writer, {"status": "error", "message": str(exc)})
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


def _send(writer: asyncio.StreamWriter, data: dict) -> None:
    raw = json.dumps(data).encode()
    writer.write(struct.pack(">I", len(raw)) + raw)


bridge = DesktopBridge()
