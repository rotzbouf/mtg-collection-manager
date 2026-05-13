#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"

echo "=== MTG Collection Manager – Setup ==="

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

# --- System dependencies (tesseract) ---
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

# --- GPU / CUDA (install before requirements so easyocr picks up the GPU torch) ---
echo ""
echo "Checking for NVIDIA GPU..."

if command -v nvidia-smi &>/dev/null; then
    CUDA_VERSION=$(nvidia-smi 2>/dev/null | grep -oP "CUDA Version: \K[0-9]+\.[0-9]+" | head -1)
    if [ -n "$CUDA_VERSION" ]; then
        CUDA_MAJOR=$(echo "$CUDA_VERSION" | cut -d. -f1)
        # Try wheels from newest to oldest; driver CUDA is forward-compatible so
        # a CUDA 13 driver can run cu126/cu124 wheels without issue.
        if   [ "$CUDA_MAJOR" -ge 13 ]; then TORCH_CUDA_LIST="cu126 cu124"
        elif [ "$CUDA_MAJOR" -ge 12 ]; then TORCH_CUDA_LIST="cu124 cu121"
        elif [ "$CUDA_MAJOR" -eq 11 ]; then TORCH_CUDA_LIST="cu118"
        else                                 TORCH_CUDA_LIST=""
        fi

        if [ -z "$TORCH_CUDA_LIST" ]; then
            echo "WARNING: CUDA $CUDA_VERSION < 11 — GPU PyTorch not supported, using CPU."
        else
            GPU_OK=0
            for TORCH_CUDA in $TORCH_CUDA_LIST; do
                echo "NVIDIA GPU found (CUDA $CUDA_VERSION) — trying PyTorch $TORCH_CUDA..."
                if "$VENV_DIR/bin/pip" install torch torchvision \
                    --index-url "https://download.pytorch.org/whl/$TORCH_CUDA"; then
                    echo "GPU PyTorch installed ($TORCH_CUDA)."
                    GPU_OK=1
                    break
                else
                    echo "  $TORCH_CUDA unavailable, trying next..."
                fi
            done
            if [ "$GPU_OK" -eq 0 ]; then
                echo "WARNING: No GPU PyTorch wheel matched CUDA $CUDA_VERSION — falling back to CPU."
            fi
        fi
    else
        echo "nvidia-smi found but CUDA version undetectable — using CPU PyTorch."
    fi
else
    echo "No NVIDIA GPU detected — EasyOCR and pHash will run on CPU."
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
echo "Activate:  source venv/bin/activate"
echo "Run:       python bot.py"
echo ""
echo "NOTE: On the very first run EasyOCR will download its language models (~150 MB)."
echo "      This happens once and is cached automatically."
