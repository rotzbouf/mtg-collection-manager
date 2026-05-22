#!/usr/bin/env bash
set -euo pipefail

# Build script for MTG Collection Manager — Nuitka --onefile edition.
# Produces a single self-contained native binary.
#
# Usage:  bash build_nuitka.sh
# Output: dist/mtg-collection-manager

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
DIST_DIR="$SCRIPT_DIR/dist"
BUILD_CACHE="$SCRIPT_DIR/build/.nuitka-cache"
APP_NAME="mtg-collection-manager"
VERSION="$(git -C "$SCRIPT_DIR" describe --tags --always 2>/dev/null || echo 'dev')"

echo "=== MTG Collection Manager — Nuitka onefile build ==="
echo "    Version: $VERSION"
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found. Run install.sh first." >&2
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/config.json" ]; then
    echo "ERROR: config.json not found in project root." >&2
    exit 1
fi

# ── Install Nuitka build dependencies ────────────────────────────────────────

echo "Installing Nuitka and build tools..."
"$VENV_PYTHON" -m pip install --upgrade \
    nuitka \
    ordered-set \
    zstandard \
    patchelf \
    --quiet

# ── Prepare output directory ──────────────────────────────────────────────────

mkdir -p "$DIST_DIR" "$BUILD_CACHE"

# ── Run Nuitka ────────────────────────────────────────────────────────────────

echo "Running Nuitka (first build: several minutes, subsequent builds: cached)..."
echo ""

cd "$SCRIPT_DIR"
"$VENV_PYTHON" -m nuitka \
    --onefile \
    --assume-yes-for-downloads \
    \
    --output-dir="$DIST_DIR" \
    --output-filename="$APP_NAME" \
    --onefile-tempdir-spec="{CACHE_DIR}/$APP_NAME/$VERSION" \
    \
    --enable-plugin=pyqt6 \
    --include-qt-plugins=sensible \
    \
    --follow-import-to=core \
    --follow-import-to=desktop \
    --follow-import-to=server \
    --follow-import-to=cogs \
    \
    --include-package=easyocr \
    --include-package=cv2 \
    --include-package=torch \
    --include-package=PIL \
    --include-package=discord \
    --include-package=aiohttp \
    --include-package=aiosqlite \
    --include-package=qasync \
    --include-package=matplotlib \
    --include-package=fastapi \
    --include-package=uvicorn \
    --include-package=starlette \
    --include-package=pytesseract \
    --include-package=anyio \
    --include-package=httpx \
    --include-package=scipy \
    \
    --nofollow-import-to=torch.cuda \
    --nofollow-import-to=torch.backends.cuda \
    --nofollow-import-to=torch.backends.cudnn \
    --nofollow-import-to=torch.utils.tensorboard \
    --nofollow-import-to=torch.distributed \
    --nofollow-import-to=torch.optim \
    --nofollow-import-to=torch.ao \
    --nofollow-import-to=torch.jit \
    --nofollow-import-to=torch.onnx \
    --nofollow-import-to=torch.export \
    --nofollow-import-to=torchaudio \
    --nofollow-import-to=torchvision.models \
    --nofollow-import-to=triton \
    --nofollow-import-to=tkinter \
    --nofollow-import-to=_tkinter \
    --nofollow-import-to=tornado \
    --nofollow-import-to=wx \
    --nofollow-import-to=gi \
    --nofollow-import-to=IPython \
    --nofollow-import-to=jupyter \
    --nofollow-import-to=notebook \
    --nofollow-import-to=pytest \
    --nofollow-import-to=setuptools \
    --nofollow-import-to=xmlrpc \
    --nofollow-import-to=ftplib \
    --nofollow-import-to=imaplib \
    --nofollow-import-to=poplib \
    --nofollow-import-to=smtplib \
    \
    --include-data-dir=images/mana=images/mana \
    --include-data-dir=server/ui/templates=server/ui/templates \
    --include-data-dir=server/ui/static=server/ui/static \
    --include-data-files=config.json=config.json \
    \
    --standalone \
    --lto=yes \
    --jobs="$(nproc)" \
    --no-progressbar \
    --python-flag=no_site \
    \
    desktop/app.py

# ── Summary ───────────────────────────────────────────────────────────────────

APP_EXE="$DIST_DIR/$APP_NAME"
APP_SIZE="$(du -sh "$APP_EXE" 2>/dev/null | cut -f1 || echo '?')"

echo ""
echo "=== Build complete ==="
echo ""
echo "  Executable: $APP_EXE"
echo "  Size:       $APP_SIZE"
echo ""
echo "To run:"
echo "  $APP_EXE"
echo ""
echo "Notes:"
echo "  - On first launch, $APP_NAME extracts to \$XDG_CACHE_HOME/$APP_NAME/$VERSION/"
echo "  - config.json is seeded from the bundle into the directory next to the exe."
echo "  - EasyOCR model download (~150 MB) happens automatically on first scan."
echo "  - Subsequent launches reuse the cached extraction (fast startup)."
