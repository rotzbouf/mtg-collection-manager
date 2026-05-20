"""
FastAPI web UI for the MTG Collection Manager.
Run:  python3 -m server.ui.app
"""
from __future__ import annotations

import os
import sys
import logging
from contextlib import asynccontextmanager

# Allow `python3 server/ui/app.py` from project root (3 levels up: ui → server → project)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import core.config as _cfg
_cfg.inject_env()
del _cfg

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps
from server.ui.routes import collection, containers, stats, import_export, images

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

templates = Jinja2Templates(directory=_TEMPLATES_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await deps.db.initialize()
    logger.info("Database ready at %s", deps.db.path)
    yield
    await deps.db.close()
    await deps.scryfall.close()
    logger.info("Shutdown complete")


app = FastAPI(title="MTG Collection Manager", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

app.include_router(collection.router)
app.include_router(containers.router)
app.include_router(stats.router)
app.include_router(import_export.router)
app.include_router(images.router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/collection")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("UI_HOST", "0.0.0.0")
    port = int(os.getenv("UI_PORT", "8080"))
    uvicorn.run(
        "server.ui.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
