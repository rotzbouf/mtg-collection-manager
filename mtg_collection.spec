# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MTG Collection Manager desktop app.
#
# Produces a self-contained directory in build/mtg-collection-manager/.
# Run via build.sh — do not invoke this file directly.

from PyInstaller.utils.hooks import collect_all

# ── Packages that need full collection (lazy imports, native extensions) ─────
_collect_all = ['easyocr', 'cv2', 'discord']

datas: list     = []
binaries: list  = []
hiddenimports: list = []

for _pkg in _collect_all:
    try:
        _d, _b, _h = collect_all(_pkg)
        datas      += _d
        binaries   += _b
        hiddenimports += _h
    except Exception:
        pass

# ── Project data files ────────────────────────────────────────────────────────
datas += [
    ('server/ui/templates', 'server/ui/templates'),
    ('server/ui/static',    'server/ui/static'),
    ('images/mana',         'images/mana'),
    ('config.json',         '.'),
]

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    ['desktop/app.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        # discord extensions loaded via load_extension()
        'discord.ext.commands',
        'discord.ext.tasks',
        'discord.app_commands',
        # uvicorn — entry-points discovered at runtime
        'uvicorn.lifespan.on',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.logging',
        # fastapi / starlette internals
        'fastapi',
        'starlette.routing',
        'starlette.staticfiles',
        'starlette.templating',
        'anyio',
        'anyio._backends._asyncio',
        # matplotlib — desktop chart (QtAgg) and bot mode (Agg)
        'matplotlib.backends.backend_qtagg',
        'matplotlib.backends.backend_agg',
        # misc
        'pytesseract',
        'aiosqlite',
        'qasync',
        'PIL._imaging',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'tornado', 'wx', 'gi'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='mtg-collection-manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    upx_exclude=[],
    name='mtg-collection-manager',
)
