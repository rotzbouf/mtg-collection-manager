#!/usr/bin/env bash
# Run from project root regardless of where the script lives
cd "$(dirname "$0")/.."

if [ ! -f venv/bin/python ]; then
    echo "ERROR: venv not found. Run server/install.sh first." >&2
    exit 1
fi

source .env 2>/dev/null || true

PORT="${UI_PORT:-8080}"

# Try to get the public IP; fall back to first local IP
EXTERNAL_IP=$(curl -s --max-time 3 ifconfig.me 2>/dev/null)
if [ -z "$EXTERNAL_IP" ]; then
    EXTERNAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
if [ -z "$EXTERNAL_IP" ]; then
    EXTERNAL_IP="<server-ip>"
fi

echo "Web UI: http://$EXTERNAL_IP:$PORT"

exec venv/bin/python -m server.ui.app
