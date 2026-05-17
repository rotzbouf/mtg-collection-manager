"""Import/Export routes."""
from __future__ import annotations

import asyncio
import io
import os
from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

import ui.deps as deps
from core.exporter import to_csv, to_json, to_moxfield
from core.importer import detect_format, parse_full_csv, parse_json, parse_moxfield_csv, normalize_row

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()


@router.get("/import-export", response_class=HTMLResponse)
async def import_export_page(request: Request):
    return templates.TemplateResponse("import_export.html", {
        "request": request,
        "error": None,
        "preview": None,
        "import_done": False,
    })


@router.post("/import", response_class=HTMLResponse)
async def import_upload(
    request: Request,
    file: Optional[UploadFile] = File(None),
    fmt: str = Form(""),
):
    # Preview phase — file is required here
    if not file or not file.filename:
        return templates.TemplateResponse("import_export.html", {
            "request": request,
            "error": "Please select a file to import.",
            "preview": None,
            "import_done": False,
        })
    content = await file.read()
    filename = file.filename or ""

    try:
        fmt_detected = await asyncio.to_thread(detect_format, filename, content)
    except ValueError as e:
        return templates.TemplateResponse("import_export.html", {
            "request": request,
            "error": str(e),
            "preview": None,
            "import_done": False,
        })

    try:
        if fmt_detected == "moxfield_csv":
            rows = await asyncio.to_thread(parse_moxfield_csv, content)
        elif fmt_detected == "full_csv":
            rows = await asyncio.to_thread(parse_full_csv, content)
        else:
            rows = await asyncio.to_thread(parse_json, content)
    except ValueError as e:
        return templates.TemplateResponse("import_export.html", {
            "request": request,
            "error": str(e),
            "preview": None,
            "import_done": False,
        })

    preview = rows[:10]
    return templates.TemplateResponse("import_export.html", {
        "request": request,
        "error": None,
        "preview": preview,
        "preview_total": len(rows),
        "parsed_json": json.dumps(rows),
        "fmt": fmt_detected,
        "import_done": False,
    })


@router.post("/import/confirm", response_class=HTMLResponse)
async def import_confirm(
    request: Request,
    parsed_json: str = Form(""),
    fmt: str = Form(""),
):
    import json
    try:
        rows = json.loads(parsed_json)
    except Exception:
        rows = []
    count = 0
    for row in rows:
        card_dict, _container_name = normalize_row(row)
        await deps.db.add_card(card_dict, added_by="web-ui-import")
        count += 1
    return templates.TemplateResponse("import_export.html", {
        "request": request,
        "error": None,
        "preview": None,
        "import_done": True,
        "import_count": count,
    })


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
