# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the KEA Physician Scheduler backend.
#
# Run from the project root (KEAsked/):
#   pyinstaller scheduler_server.spec --distpath dist_backend --workpath build_pyinstaller --noconfirm
#
# Output: dist_backend/scheduler_server  (or scheduler_server.exe on Windows)

import glob
import os

block_cipher = None

# ---------------------------------------------------------------------------
# Collect ortools binaries.
#
# ortools ships its C++ DLLs in a `.libs` subdirectory alongside the Python
# package.  collect_all() misses this dot-prefixed directory, so we gather
# those DLLs explicitly and place them at the bundle root where the OS DLL
# loader can find them.
# ---------------------------------------------------------------------------
from PyInstaller.utils.hooks import collect_all
_ortools_datas, _ortools_binaries, _ortools_hiddenimports = collect_all('ortools')

try:
    import ortools as _ortools_pkg
    _ortools_libs_dir = os.path.join(os.path.dirname(_ortools_pkg.__file__), '.libs')
    _libs_dlls = [(dll, '.') for dll in glob.glob(os.path.join(_ortools_libs_dir, '*.dll'))]
except Exception:
    _libs_dlls = []

a = Analysis(
    ['scheduler/api/server.py'],
    pathex=['.'],
    binaries=_ortools_binaries + _libs_dlls,
    datas=_ortools_datas,
    hiddenimports=_ortools_hiddenimports + [
        # uvicorn dynamic imports
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        # anyio backends
        'anyio',
        'anyio._backends._asyncio',
        'anyio._backends._trio',
        # FastAPI / starlette internals
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        # pydantic
        'pydantic',
        'pydantic.v1',
        # scheduler package — explicitly include all sub-modules so PyInstaller
        # doesn't miss them (they are imported dynamically in some code paths).
        'scheduler',
        'scheduler.api',
        'scheduler.api.server',
        'scheduler.api.schemas',
        'scheduler.backend',
        'scheduler.backend.config',
        'scheduler.backend.generator',
        'scheduler.backend.importer',
        'scheduler.backend.importer_flat',
        'scheduler.backend.models',
        'scheduler.backend.shifts',
        'scheduler.backend.validator',
        'scheduler.backend.generator_cpsat',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'PIL',
        'PyQt5',
        'wx',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='scheduler_server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX compression breaks native extensions like ortools .pyd files
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
