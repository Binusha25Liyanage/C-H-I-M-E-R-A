# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['d:/projects/Project C H I M E R A/backend/app.py'],
    pathex=[],
    binaries=[],
    datas=[('d:/projects/Project C H I M E R A/frontend', 'frontend')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Chimera',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['d:/projects/Project C H I M E R A/frontend/assets/chimera.ico'],
)
