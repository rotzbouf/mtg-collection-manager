#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="mtg-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "=== MTG Bot – Service Deinstallation ==="

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Run with sudo: sudo bash service_uninstall.sh" >&2
    exit 1
fi

if [ ! -f "$SERVICE_FILE" ]; then
    echo "Service $SERVICE_NAME is not installed." >&2
    exit 1
fi

systemctl stop    "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "$SERVICE_FILE"
systemctl daemon-reload

echo "Service $SERVICE_NAME removed."
