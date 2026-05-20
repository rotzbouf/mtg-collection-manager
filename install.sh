#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

echo "=== MTG Collection Manager – Desktop Setup ==="

# --- Check Python ---
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.10+." >&2
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    echo "ERROR: Python 3.10+ required (found: $PYTHON_VERSION)." >&2
    exit 1
fi

echo "Python $PYTHON_VERSION found."

# --- System dependencies ---
echo ""
echo "Checking system dependencies..."

MISSING_PKGS=()

if ! command -v tesseract &>/dev/null; then
    MISSING_PKGS+=(tesseract-ocr tesseract-ocr-deu)
fi

if command -v dpkg &>/dev/null; then
    # OpenCV / EasyOCR runtime
    dpkg -s libgl1          &>/dev/null || MISSING_PKGS+=(libgl1)
    dpkg -s libglib2.0-0    &>/dev/null || MISSING_PKGS+=(libglib2.0-0)
    # Qt6 xcb platform plugin (needed by PyQt6 on X11 desktops)
    dpkg -s libxcb-cursor0  &>/dev/null || MISSING_PKGS+=(libxcb-cursor0)
    dpkg -s libxcb-icccm4   &>/dev/null || MISSING_PKGS+=(libxcb-icccm4)
    dpkg -s libxcb-image0   &>/dev/null || MISSING_PKGS+=(libxcb-image0)
    dpkg -s libxcb-keysyms1 &>/dev/null || MISSING_PKGS+=(libxcb-keysyms1)
    dpkg -s libxcb-randr0   &>/dev/null || MISSING_PKGS+=(libxcb-randr0)
    dpkg -s libxcb-render-util0 &>/dev/null || MISSING_PKGS+=(libxcb-render-util0)
    dpkg -s libxkbcommon-x11-0  &>/dev/null || MISSING_PKGS+=(libxkbcommon-x11-0)
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Installing system packages: ${MISSING_PKGS[*]}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y "${MISSING_PKGS[@]}"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y tesseract tesseract-langpack-deu mesa-libGL glib2 \
            xcb-util-cursor xcb-util-icccm xcb-util-image xcb-util-keysyms libxkbcommon-x11
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm tesseract tesseract-data-deu mesa \
            xcb-util-cursor xcb-util-icccm xcb-util-image xcb-util-keysyms libxkbcommon-x11
    else
        echo "WARNING: No supported package manager found. Install tesseract and Qt6 xcb libs manually." >&2
    fi
else
    echo "System dependencies OK."
fi

# --- Virtual environment ---
echo ""
if [ -d "$VENV_DIR" ]; then
    echo "Existing venv found — skipping creation."
else
    echo "Creating venv at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
fi

# --- pip dependencies ---
echo ""
echo "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/deps/requirements.txt"

# --- Create config.json if missing ---
echo ""
if [ ! -f "$PROJECT_DIR/config.json" ]; then
    cp "$PROJECT_DIR/config.json.example" "$PROJECT_DIR/config.json"
    echo "config.json created from config.json.example."
    echo "  → Open Settings → Configuration in the app to fill in your Discord token and channel IDs."
else
    echo "config.json already exists."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start the app:"
echo "  bash start_desktop.sh"
echo ""
echo "To also run the Discord bot or Web UI, use Settings → Services inside the app."
echo ""
echo "Headless / server setup (no display): see server/install.sh instead."
echo ""
echo "NOTE: On the first scan, EasyOCR will download its language models (~150 MB)."
echo "      This happens once and is cached automatically."
