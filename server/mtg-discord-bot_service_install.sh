#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mtg-bot"
# Resolve project root (one level above this script)
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$USER}"

echo "=== MTG Bot – Service Installation ==="

# --- Checks ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run with sudo: sudo bash server/mtg-discord-bot_service_install.sh" >&2
    exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found. Run server/install.sh first." >&2
    exit 1
fi

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "ERROR: .env not found. Copy .env.example to .env and fill in your tokens." >&2
    exit 1
fi

if ! grep -q "^DISCORD_TOKEN=." "$PROJECT_DIR/.env"; then
    echo "ERROR: DISCORD_TOKEN is not set in .env." >&2
    exit 1
fi

# --- Create service file ---
echo "Creating $SERVICE_FILE ..."

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=MTG Collection Manager Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV_PYTHON $PROJECT_DIR/server/bot.py
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
echo "Status:  sudo systemctl status $SERVICE_NAME"
echo "Logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "Update:  git pull && sudo systemctl restart $SERVICE_NAME"
