#!/usr/bin/env bash
set -euo pipefail

# Build script for MTG Collection Manager desktop app.
# Produces a self-contained directory under build/.
#
# Usage:  bash build.sh
# Output: build/mtg-collection-manager/mtg-collection-manager

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
BUILD_DIR="$SCRIPT_DIR/build"
APP_NAME="mtg-collection-manager"

echo "=== MTG Collection Manager — Desktop Build ==="
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

# ── Install PyInstaller into the project venv ─────────────────────────────────

echo "Ensuring PyInstaller is installed..."
"$VENV_PYTHON" -m pip install --upgrade pyinstaller --quiet

# ── Clean previous build artifacts ───────────────────────────────────────────

echo "Cleaning previous build..."
rm -rf "$BUILD_DIR/$APP_NAME" "$BUILD_DIR/.work"
mkdir -p "$BUILD_DIR"

# ── Run PyInstaller ───────────────────────────────────────────────────────────

echo "Running PyInstaller (this may take several minutes on first build)..."
echo ""

cd "$SCRIPT_DIR"
"$VENV_PYTHON" -m PyInstaller \
    --distpath "$BUILD_DIR" \
    --workpath "$BUILD_DIR/.work" \
    --noconfirm \
    mtg_collection.spec

# ── Clean work directory ──────────────────────────────────────────────────────

rm -rf "$BUILD_DIR/.work"

# ── Summary ───────────────────────────────────────────────────────────────────

APP_EXE="$BUILD_DIR/$APP_NAME/$APP_NAME"
APP_SIZE="$(du -sh "$BUILD_DIR/$APP_NAME" 2>/dev/null | cut -f1 || echo '?')"

echo ""
echo "=== Build complete ==="
echo ""
echo "  Location:   $BUILD_DIR/$APP_NAME/"
echo "  Executable: $APP_EXE"
echo "  Size:       $APP_SIZE"
echo ""
echo "To run:"
echo "  cd \"$BUILD_DIR/$APP_NAME\" && ./$APP_NAME"
echo ""
echo "Notes:"
echo "  - config.json is seeded from the bundle on first run — edit it in place."
echo "  - Copy db/mtg_collection.db to the app directory to migrate existing data."
echo "  - EasyOCR model download (~150 MB) happens automatically on first scan."
