# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the KEA Physician Scheduler backend.
#
# Run from the project root (KEAsked/):
#   pyinstaller scheduler_server.spec --distpath dist_backend --workpath build_pyinstaller --noconfirm
#
# Output: dist_backend/scheduler_server  (or scheduler_server.exe on Windows)

block_cipher = None

a = Analysis(
    ['scheduler/api/server.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
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
        # ortools / CP-SAT
        'ortools',
        'ortools.sat',
        'ortools.sat.python',
        'ortools.sat.python.cp_model',
        'ortools.util',
        'ortools.util.python',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'pandas',
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
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
