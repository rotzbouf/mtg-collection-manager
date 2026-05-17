#!/usr/bin/env bash
cd "$(dirname "$0")"
source .env 2>/dev/null || true
exec python3 -m ui.app
