#!/usr/bin/env bash
# Build the Python backend into a standalone macOS/Linux executable.
# Run this from the project root (KEAsked/) before running npm run package:mac
#
# Requirements: pip install pyinstaller
#
set -e
cd "$(dirname "$0")"

echo "[build_backend] Installing/upgrading PyInstaller..."
pip install --quiet --upgrade pyinstaller

echo "[build_backend] Bundling Python backend..."
pyinstaller scheduler_server.spec \
    --distpath dist_backend \
    --workpath build_pyinstaller \
    --noconfirm

echo "[build_backend] Done. Output: dist_backend/scheduler_server"
