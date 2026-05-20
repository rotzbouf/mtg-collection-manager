# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for MTG Collection Manager desktop app.
#
# Produces a self-contained directory in build/mtg-collection-manager/.
# Run via build.sh — do not invoke this file directly.

import os
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

# ── Drop CUDA / Triton binaries — EasyOCR runs with gpu=False ────────────────
# These are pulled in transitively by easyocr→torch but waste 2–3 GB of space.
_CUDA_STEMS = {
    # Heavy CUDA compute libs — not needed for gpu=False inference
    'libtorch_cuda', 'libtorch_cuda_linalg', 'libtorch_nvshmem',
    'libc10_cuda', 'libcaffe2_nvrtc', 'libnvrtc',
    # CUDA profiler / performance tooling
    'libcupti', 'libnvperf_host', 'libnvperf_target', 'libpcsamplingutil',
    'libcheckpoint',
    # NOTE: libcudart is intentionally kept — libtorch_global_deps.so
    # links against it at load time even for CPU-only execution.
}

def _is_gpu_binary(src: str) -> bool:
    name = os.path.basename(src).lower()
    stem = name.split('.')[0]
    # Drop any lib whose stem matches a known CUDA lib
    if stem in _CUDA_STEMS:
        return True
    norm = src.replace('\\', '/').lower()
    # Drop the entire triton package
    if '/triton/' in norm or '/triton.' in norm:
        return True
    # Drop the cuda Python package (torch.cuda bindings)
    if norm.startswith('cuda/') or '/cuda/' in norm:
        return True
    return False

binaries = [(src, dst) for src, dst in binaries if not _is_gpu_binary(src)]

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
    excludes=[
        'tkinter', '_tkinter', 'tornado', 'wx', 'gi',
        # EasyOCR runs with gpu=False — drop CUDA/Triton to keep bundle lean
        'torch.cuda',
        'torch.backends.cuda',
        'torch.backends.cudnn',
        'torch.utils.tensorboard',
        'triton',
        'torchvision.models',
        'torchaudio',
    ],
    noarchive=False,
    optimize=0,
)

# ── Post-analysis: strip CUDA / Triton from dependency-resolved binaries ─────
# Analysis discovers these transitively from torch .so dependencies even though
# we excluded them from the input binaries list above.
a.binaries = TOC([
    (name, src, typ)
    for name, src, typ in a.binaries
    if not _is_gpu_binary(name)
])

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
