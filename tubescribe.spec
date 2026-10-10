# -*- mode: python ; coding: utf-8 -*-
# pyinstaller tubescribe.spec --clean

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("yt_dlp.extractor.youtube")
    + ["tubescribe.server"]
)

a = Analysis(
    ["tubescribe/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[("tubescribe/web", "tubescribe/web")] + collect_data_files("yt_dlp"),
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "Cryptodome", "mutagen", "brotli", "websockets"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TubeScribe",
    icon="assets/tubescribe.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX causes antivirus false positives
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
