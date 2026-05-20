#!/usr/bin/env bash
cd "$(dirname "$0")/.."

if [ ! -f venv/bin/python ]; then
    echo "ERROR: venv not found. Run server/install.sh first." >&2
    exit 1
fi

exec venv/bin/python server/bot.py
