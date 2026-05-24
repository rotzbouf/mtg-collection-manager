"""Import/Export + Backup routes."""
from __future__ import annotations

import asyncio
import io
import json
import lzma
import os
import tempfile
import time as _time
import uuid
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps
from core.database import Database
from core.exporter import to_csv, to_json, to_moxfield
from core.importer import detect_format, parse_full_csv, parse_json, parse_moxfield_csv, normalize_row
from server.ui.csrf import verify_csrf

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

# C-1: size caps
_MAX_UPLOAD_BYTES  = 64 * 1024 * 1024   # 64 MB raw upload
_MAX_RESTORE_BYTES = 256 * 1024 * 1024  # 256 MB after decompression

# C-2: server-side import session store
_IMPORT_TMP: dict[str, list[dict]] = {}  # token -> parsed rows

# M-3: restore temp store with timestamps
_RESTORE_TMP: dict[str, tuple[str, float]] = {}  # token -> (tmp_path, created_at)
_RESTORE_TTL = 600  # 10 minutes


def _evict_restore_tmp():
    now = _time.monotonic()
    expired = [k for k, (path, ts) in _RESTORE_TMP.items() if now - ts > _RESTORE_TTL]
    for k in expired:
        path, _ = _RESTORE_TMP.pop(k)
        try:
            os.unlink(path)
        except OSError:
            pass


# H-4: proper file-handle helpers for asyncio.to_thread
def _write_tmp(path, content):
    with open(path, "wb") as f:
        f.write(content)


def _read_tmp(path):
    with open(path, "rb") as f:
        return f.read()


def _render(request: Request, ctx: dict):
    return templates.TemplateResponse(request, "import_export.html", ctx)


def _base_ctx():
    return {
        "error": None,
        "preview": None,
        "import_done": False,
        "backup_restore_preview": None,
        "backup_restore_token": None,
        "backup_done": False,
    }


@router.get("/import-export", response_class=HTMLResponse)
async def import_export_page(request: Request):
    return _render(request, _base_ctx())


# ── Export ────────────────────────────────────────────────────────────────────

@router.get("/export/csv")
async def export_csv():
    cards = await deps.db.get_all()
    csv_text = await asyncio.to_thread(to_csv, cards)
    return StreamingResponse(
        io.BytesIO(csv_text.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mtg_collection.csv"},
    )


@router.get("/export/json")
async def export_json():
    cards = await deps.db.get_all()
    json_text = await asyncio.to_thread(to_json, cards)
    return StreamingResponse(
        io.BytesIO(json_text.encode("utf-8")),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=mtg_collection.json"},
    )


@router.get("/export/moxfield")
async def export_moxfield():
    cards = await deps.db.get_all()
    csv_text = await asyncio.to_thread(to_moxfield, cards)
    return StreamingResponse(
        io.BytesIO(csv_text.encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=mtg_moxfield.csv"},
    )


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/import", response_class=HTMLResponse)
async def import_upload(
    request: Request,
    file: Optional[UploadFile] = File(None),
    fmt: str = Form(""),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    if not file or not file.filename:
        ctx = _base_ctx()
        ctx["error"] = "Please select a file to import."
        return _render(request, ctx)

    # C-1: size cap before reading
    if int(request.headers.get("content-length", 0)) > _MAX_UPLOAD_BYTES:
        return templates.TemplateResponse("error.html", {"request": request, "error": "Upload too large (max 64 MB)"}, status_code=413)

    content = await file.read()
    filename = file.filename or ""

    try:
        fmt_detected = await asyncio.to_thread(detect_format, filename, content)
    except ValueError as e:
        ctx = _base_ctx()
        ctx["error"] = str(e)
        return _render(request, ctx)

    try:
        if fmt_detected == "moxfield_csv":
            rows = await asyncio.to_thread(parse_moxfield_csv, content)
        elif fmt_detected == "full_csv":
            rows = await asyncio.to_thread(parse_full_csv, content)
        else:
            rows = await asyncio.to_thread(parse_json, content)
    except ValueError as e:
        ctx = _base_ctx()
        ctx["error"] = str(e)
        return _render(request, ctx)

    # C-2: store parsed rows server-side, pass token to template
    token = str(_uuid.uuid4())
    _IMPORT_TMP[token] = rows

    ctx = _base_ctx()
    ctx.update({
        "preview": rows[:10],
        "preview_total": len(rows),
        "import_token": token,
        "fmt": fmt_detected,
    })
    return _render(request, ctx)


@router.post("/import/confirm", response_class=HTMLResponse)
async def import_confirm(
    request: Request,
    import_token: str = Form(...),
    fmt: str = Form(""),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    # C-2: retrieve rows from server-side store
    rows = _IMPORT_TMP.pop(import_token, None)
    if rows is None:
        ctx = _base_ctx()
        ctx["error"] = "Import session expired or invalid. Please re-upload the file."
        return _render(request, ctx)
    count = 0
    for row in rows:
        card_dict, _container_name = normalize_row(row)
        await deps.db.add_card(card_dict, added_by="web-ui-import")
        count += 1
    ctx = _base_ctx()
    ctx.update({"import_done": True, "import_count": count})
    return _render(request, ctx)


# ── Backup ────────────────────────────────────────────────────────────────────

@router.get("/backup/download")
async def backup_download():
    data = await deps.db.backup_bytes()
    xz_data = await asyncio.to_thread(lambda: lzma.compress(data, preset=6))
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"mtg_collection_{ts}.db.xz"
    return StreamingResponse(
        io.BytesIO(xz_data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/backup/restore", response_class=HTMLResponse)
async def backup_restore_upload(
    request: Request,
    file: Optional[UploadFile] = File(None),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    _evict_restore_tmp()  # M-3: evict expired entries

    if not file or not file.filename:
        ctx = _base_ctx()
        ctx["error"] = "Please select a backup file (.db, .db.gz, or .db.xz)."
        return _render(request, ctx)

    fname = file.filename or ""
    if not (fname.endswith(".db") or fname.endswith(".db.gz") or fname.endswith(".db.xz")):
        ctx = _base_ctx()
        ctx["error"] = "Unsupported file type. Please upload a .db, .db.gz, or .db.xz backup."
        return _render(request, ctx)

    raw = await file.read()

    # C-1: cap raw upload size
    if len(raw) > _MAX_UPLOAD_BYTES:
        return templates.TemplateResponse("error.html", {"request": request, "error": "Upload too large (max 64 MB)"}, status_code=413)

    try:
        if fname.endswith(".xz"):
            decompressed = await asyncio.to_thread(lzma.decompress, raw)
            if len(decompressed) > _MAX_RESTORE_BYTES:
                return templates.TemplateResponse("error.html", {"request": request, "error": "Decompressed backup too large (max 256 MB)"}, status_code=413)
            data = decompressed
        elif fname.endswith(".gz"):
            import gzip
            decompressed = await asyncio.to_thread(gzip.decompress, raw)
            if len(decompressed) > _MAX_RESTORE_BYTES:
                return templates.TemplateResponse("error.html", {"request": request, "error": "Decompressed backup too large (max 256 MB)"}, status_code=413)
            data = decompressed
        else:
            data = raw
        counts = await Database.inspect_backup(data)
    except Exception as e:
        ctx = _base_ctx()
        ctx["error"] = f"Could not read backup: {e}"
        return _render(request, ctx)

    # Save to temp file so we don't need to pass raw bytes through the form
    token = uuid.uuid4().hex
    tmp_path = os.path.join(tempfile.gettempdir(), f"mtg_restore_{token}.db")
    await asyncio.to_thread(_write_tmp, tmp_path, data)  # H-4: proper file handle
    _RESTORE_TMP[token] = (tmp_path, _time.monotonic())  # M-3: store with timestamp

    ctx = _base_ctx()
    ctx.update({
        "backup_restore_preview": {
            "cards": counts.get("cards", 0),
            "containers": counts.get("containers", 0),
            "size_mb": len(raw) / 1024 / 1024,
            "filename": fname,
        },
        "backup_restore_token": token,
    })
    return _render(request, ctx)


@router.post("/backup/restore/confirm", response_class=HTMLResponse)
async def backup_restore_confirm(
    request: Request,
    token: str = Form(""),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    _evict_restore_tmp()  # M-3: evict expired entries

    # M-3: look up with TTL awareness
    entry = _RESTORE_TMP.pop(token, None)
    if entry is None:
        tmp_path = None
    else:
        tmp_path, created_at = entry
        if _time.monotonic() - created_at > _RESTORE_TTL:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            tmp_path = None

    if not tmp_path or not os.path.exists(tmp_path):
        ctx = _base_ctx()
        ctx["error"] = "Restore session expired. Please re-upload the backup file."
        return _render(request, ctx)

    try:
        data = await asyncio.to_thread(_read_tmp, tmp_path)  # H-4: proper file handle
        await deps.db.restore_from_bytes(data)
    except Exception as e:
        ctx = _base_ctx()
        ctx["error"] = f"Restore failed: {e}"
        return _render(request, ctx)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    ctx = _base_ctx()
    ctx["backup_done"] = True
    return _render(request, ctx)
