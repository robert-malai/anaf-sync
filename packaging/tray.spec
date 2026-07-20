# PyInstaller spec for the anaf-sync desktop tray companion.
#
# One spec, platform conditionals. Build from the repo root with:
#     uv run pyinstaller packaging/tray.spec
#
# Produces a windowed (no-console) app: a menu-bar-only .app on macOS
# (LSUIElement=1, no Dock icon), a one-dir windowed exe on Windows, and a
# one-dir binary on Linux. Unused Qt modules are excluded to keep size sane.
#
# Known gap (out of scope): code signing + notarization (macOS) and
# Authenticode (Windows). Unsigned bundles trigger OS warnings — see README for
# the right-click-open workaround.

import sys

block_cipher = None

# Qt modules the tray never uses; excluding them trims tens of MB.
_EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
    "PySide6.QtPdf",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtSensors",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSql",
    "PySide6.QtTest",
]

a = Analysis(
    ["tray_entry.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["anaf_sync.tray.app"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="anaf-sync-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed / no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="anaf-sync-tray",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="anaf-sync-tray.app",
        icon=None,
        bundle_identifier="ro.anaf-sync.tray",
        info_plist={
            # Menu-bar-only: no Dock icon, no app-switcher entry.
            "LSUIElement": True,
            "CFBundleShortVersionString": "0.1.3",
            "NSHighResolutionCapable": True,
        },
    )
