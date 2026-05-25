#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -f venv/bin/python ]; then
    echo "ERROR: venv not found. Run install.sh first." >&2
    exit 1
fi

# Prevent Qt's fontconfig from printing "Cannot load default config file: No such file: (null)"
# when FONTCONFIG_FILE is unset in the launch environment.
export FONTCONFIG_FILE="${FONTCONFIG_FILE:-/etc/fonts/fonts.conf}"

exec venv/bin/python -m desktop.app
