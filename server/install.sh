#!/usr/bin/env bash
set -euo pipefail

# Resolve project root (one level above this script)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

echo "=== MTG Collection Manager – Headless Server Setup ==="
echo "(For a desktop/GUI installation run install.sh from the project root instead.)"
echo ""

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

# opencv-python-headless needs libGL and libglib2.0 on minimal Linux installs
if command -v dpkg &>/dev/null; then
    dpkg -s libgl1 &>/dev/null       || MISSING_PKGS+=(libgl1)
    dpkg -s libglib2.0-0 &>/dev/null || MISSING_PKGS+=(libglib2.0-0)
fi

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Installing system packages: ${MISSING_PKGS[*]}"
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y "${MISSING_PKGS[@]}"
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y tesseract tesseract-langpack-deu mesa-libGL glib2
    elif command -v pacman &>/dev/null; then
        sudo pacman -S --noconfirm tesseract tesseract-data-deu mesa
    else
        echo "WARNING: No supported package manager found. Please install tesseract-ocr and libGL manually." >&2
    fi
else
    echo "System dependencies already installed (tesseract: $(tesseract --version 2>&1 | head -1))."
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
    echo "config.json created from config.json.example — set your Discord token and channel IDs."
else
    echo "config.json already exists."
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps (headless server):"
echo "  1. Edit $PROJECT_DIR/.env — set DISCORD_TOKEN and channel IDs."
echo "  2. Install systemd services (run whichever you need):"
echo "       sudo bash server/mtg-discord-bot_service_install.sh"
echo "       sudo bash server/mtg-webui_service_install.sh"
echo "  3. Or start manually without a service:"
echo "       bash server/start_mtg-discord-bot.sh"
echo "       bash server/start_ui.sh"
echo ""
echo "NOTE: On the very first scan EasyOCR will download its language models (~150 MB)."
echo "      This happens once and is cached automatically."
