"""Database backup and restore operations."""
import asyncio
import os
import shutil
import sqlite3
import tempfile


class _BackupMixin:
    """Requires: self._db, self._write_lock, self._restore_lock, self.path, self.initialize(), self.close()."""

    async def backup_bytes(self) -> bytes:
        """Return a consistent online snapshot of the database as raw bytes."""
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(tmp_fd)
        try:
            def _do():
                src = sqlite3.connect(self.path)
                dst = sqlite3.connect(tmp_path)
                src.backup(dst)
                dst.close()
                src.close()
                with open(tmp_path, "rb") as f:
                    return f.read()
            return await asyncio.to_thread(_do)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    @staticmethod
    async def inspect_backup(data: bytes) -> dict:
        """Validate backup bytes and return {"cards": N, "containers": N}.

        Raises ValueError if the data is not a valid MTG Collection Manager DB.
        """
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(tmp_fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(data)

            def _check():
                con = sqlite3.connect(tmp_path)
                try:
                    tables = {
                        r[0]
                        for r in con.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    if "collection" not in tables:
                        raise ValueError("Not a valid MTG Collection Manager backup")
                    cards = con.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
                    containers = (
                        con.execute("SELECT COUNT(*) FROM containers").fetchone()[0]
                        if "containers" in tables
                        else 0
                    )
                    return {"cards": cards, "containers": containers}
                finally:
                    con.close()

            return await asyncio.to_thread(_check)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    async def restore_from_bytes(self, data: bytes) -> None:
        """Replace the current database with *data* and reinitialize.

        Acquires both locks: _write_lock blocks ongoing writes; _restore_lock
        prevents concurrent restores.
        """
        async with self._write_lock:
            async with self._restore_lock:
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
                os.close(tmp_fd)
                try:
                    with open(tmp_path, "wb") as f:
                        f.write(data)
                    await self.close()
                    shutil.move(tmp_path, self.path)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                await self.initialize()
