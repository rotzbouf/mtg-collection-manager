#!/usr/bin/env bash
set -euo pipefail

# Resolve project root (one level above this script)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

echo "=== MTG Collection Manager – Server Setup ==="

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
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# --- Create .env if missing ---
echo ""
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo ".env created from .env.example — please fill in DISCORD_TOKEN and channel IDs."
else
    echo ".env already exists."
fi

echo ""
echo "=== Setup complete ==="
echo "Next: edit $PROJECT_DIR/.env, then run:"
echo "  sudo bash $(dirname "${BASH_SOURCE[0]}")/service_install.sh"
echo ""
echo "NOTE: On the very first run EasyOCR will download its language models (~150 MB)."
echo "      This happens once and is cached automatically."
