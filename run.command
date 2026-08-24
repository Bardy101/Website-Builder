#!/bin/bash
# Double-click this file (macOS) to open the prospect finder.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
    python3 run.py
else
    echo "Python 3 was not found. Install it from https://www.python.org/downloads/"
    read -r -p "Press Enter to close…"
    exit 1
fi
