@echo off
REM Build the Python backend into a standalone Windows executable.
REM Run this from the project root (KEAsked\) before running npm run package:win
REM
REM Requirements: pip install pyinstaller
REM
cd /d "%~dp0"

echo [build_backend] Installing/upgrading PyInstaller...
pip install --quiet --upgrade pyinstaller

echo [build_backend] Bundling Python backend...
pyinstaller scheduler_server.spec ^
    --distpath dist_backend ^
    --workpath build_pyinstaller ^
    --noconfirm

if %ERRORLEVEL% NEQ 0 (
    echo [build_backend] ERROR: PyInstaller failed.
    exit /b 1
)

echo [build_backend] Done. Output: dist_backend\scheduler_server.exe
