"""Import/Export + Backup routes."""
from __future__ import annotations

import asyncio
import io
import json
import lzma
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps
from core.database import Database
from core.exporter import to_csv, to_json, to_moxfield
from core.importer import detect_format, parse_full_csv, parse_json, parse_moxfield_csv, normalize_row

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

_RESTORE_TMP: dict[str, str] = {}  # token → tmp file path


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
):
    if not file or not file.filename:
        ctx = _base_ctx()
        ctx["error"] = "Please select a file to import."
        return _render(request, ctx)

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

    ctx = _base_ctx()
    ctx.update({
        "preview": rows[:10],
        "preview_total": len(rows),
        "parsed_json": json.dumps(rows),
        "fmt": fmt_detected,
    })
    return _render(request, ctx)


@router.post("/import/confirm", response_class=HTMLResponse)
async def import_confirm(
    request: Request,
    parsed_json: str = Form(""),
    fmt: str = Form(""),
):
    try:
        rows = json.loads(parsed_json)
    except Exception:
        rows = []
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
):
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
    try:
        if fname.endswith(".xz"):
            data = await asyncio.to_thread(lzma.decompress, raw)
        elif fname.endswith(".gz"):
            import gzip
            data = await asyncio.to_thread(gzip.decompress, raw)
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
    await asyncio.to_thread(open(tmp_path, "wb").write, data)
    _RESTORE_TMP[token] = tmp_path

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
):
    tmp_path = _RESTORE_TMP.pop(token, None)
    if not tmp_path or not os.path.exists(tmp_path):
        ctx = _base_ctx()
        ctx["error"] = "Restore session expired. Please re-upload the backup file."
        return _render(request, ctx)

    try:
        data = await asyncio.to_thread(lambda: open(tmp_path, "rb").read())
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
