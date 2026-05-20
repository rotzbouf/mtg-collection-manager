#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mtg-webui"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$USER}"

echo "=== MTG Web UI – Service Installation ==="

# --- Checks ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run with sudo: sudo bash server/mtg-webui_service_install.sh" >&2
    exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found. Run server/install.sh first." >&2
    exit 1
fi

# Read port/host from .env if present, fall back to defaults
UI_PORT="${UI_PORT:-8080}"
UI_HOST="${UI_HOST:-0.0.0.0}"
if [ -f "$PROJECT_DIR/.env" ]; then
    LOADED_PORT=$(grep -E '^UI_PORT=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]') || true
    LOADED_HOST=$(grep -E '^UI_HOST=' "$PROJECT_DIR/.env" | cut -d= -f2 | tr -d '[:space:]') || true
    [ -n "$LOADED_PORT" ] && UI_PORT="$LOADED_PORT"
    [ -n "$LOADED_HOST" ] && UI_HOST="$LOADED_HOST"
fi

# --- Create service file ---
echo "Creating $SERVICE_FILE ..."

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=MTG Collection Manager Web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
Environment=UI_PORT=$UI_PORT
Environment=UI_HOST=$UI_HOST
ExecStart=$VENV_PYTHON -m server.ui.app
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# --- Enable and start ---
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start  "$SERVICE_NAME"

echo ""
echo "=== Done ==="
echo "URL:     http://<server-ip>:$UI_PORT"
echo "Status:  sudo systemctl status $SERVICE_NAME"
echo "Logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "Update:  git pull && sudo systemctl restart $SERVICE_NAME"
