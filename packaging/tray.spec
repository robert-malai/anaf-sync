# PyInstaller spec for the anaf-sync desktop bundle.
#
# One spec, platform conditionals. Build from the repo root with:
#     uv run pyinstaller packaging/tray.spec
#
# Produces a one-dir bundle carrying TWO executables:
#
#   anaf-sync-tray   windowed / no console — the desktop companion
#   anaf-sync        console — the CLI, doing every bit of the actual work
#
# Both, deliberately. The tray never syncs in-process: `tray/runner.py` spawns
# `anaf-sync <subcommand>` as a child, and `scheduling.py` registers that same
# executable with schtasks / systemd / launchd. A tray-only bundle ships a
# "Sincronizează acum" button that cannot work and a schedule that cannot be
# installed. `scheduling.resolve_script` finds the CLI next to `sys.executable`
# — which is exactly where COLLECT puts it (and, on macOS, Contents/MacOS/).
#
# The result is a menu-bar-only .app on macOS (LSUIElement=1, no Dock icon), a
# one-dir windowed exe on Windows, and a one-dir binary on Linux. Unused Qt
# modules are excluded to keep size sane. Downstream packaging — the Inno Setup
# installer and the macOS .dmg — consumes this directory as-is; see
# `windows-setup.iss` and `make_dmg.sh`.
#
# Known gap (out of scope): code signing + notarization (macOS) and
# Authenticode (Windows). Unsigned bundles trigger OS warnings — see README for
# the per-platform workaround.

import os
import sys

# The version the bundle reports comes from the package it bundles — a spec is
# executed by the build venv's Python, where `uv sync` has already installed
# anaf_sync, so there is nothing to keep in step by hand.
from anaf_sync import __version__

# Entry scripts are resolved against the spec's own directory (PyInstaller
# injects SPECPATH), not the working directory the build was launched from.
_HERE = SPECPATH  # noqa: F821 - injected by PyInstaller

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


def _analyze(entry, hiddenimports):
    """Analyse one entry script with the settings both executables share."""
    return Analysis(  # noqa: F821 - injected by PyInstaller
        [os.path.join(_HERE, entry)],
        pathex=[],
        binaries=[],
        datas=[],
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=_EXCLUDES,
        noarchive=False,
    )


# Two analyses rather than MERGE(): MERGE rewrites the second program's
# dependencies into *references* resolved relative to a sibling directory,
# which is the multi-directory layout we explicitly do not want. Feeding both
# into one COLLECT gives one directory whose binaries are the union — and
# PyInstaller de-duplicates by destination name, so the Qt payload the CLI does
# not use is still collected exactly once.
tray_a = _analyze("tray_entry.py", ["anaf_sync.tray.app"])
cli_a = _analyze("cli_entry.py", [])

tray_pyz = PYZ(tray_a.pure, tray_a.zipped_data)  # noqa: F821
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data)  # noqa: F821

tray_exe = EXE(  # noqa: F821
    tray_pyz,
    tray_a.scripts,
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

cli_exe = EXE(  # noqa: F821
    cli_pyz,
    cli_a.scripts,
    [],
    exclude_binaries=True,
    name="anaf-sync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # a CLI: it must be able to print to a terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# Argument order here is load-bearing on macOS, in a way that is invisible on
# the other two platforms — see the BUNDLE call below. The CLI goes FIRST and
# the tray LAST: COLLECT inherits `console` from whichever EXE it saw last, and
# a COLLECT that reports console=True makes BUNDLE write LSBackgroundOnly into
# the plist. That flag is not a cosmetic sibling of LSUIElement: it pins the
# process to the *prohibited* activation policy, so Facturi and Setări could
# never take focus — the exact failure `tray/macos.py` documents.
coll = COLLECT(  # noqa: F821
    cli_exe,
    cli_a.binaries,
    cli_a.zipfiles,
    cli_a.datas,
    tray_exe,
    tray_a.binaries,
    tray_a.zipfiles,
    tray_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="anaf-sync-tray",
)

if sys.platform == "darwin":
    # `tray_exe` is passed explicitly, and BEFORE `coll`, for the other half of
    # the same problem: BUNDLE picks CFBundleExecutable from the *first*
    # EXECUTABLE entry in its TOC, and COLLECT's TOC leads with the CLI. Left
    # to the COLLECT alone, double-clicking the .app would run `anaf-sync` with
    # no arguments — a console program that prints help to a terminal nobody
    # opened — instead of starting the tray. Passing it first fixes the name;
    # passing it *before* `coll` leaves the console/LSBackgroundOnly setting to
    # the COLLECT, which is why the ordering above matters too. The duplicate
    # TOC entry this creates is de-duplicated by `normalize_toc`.
    #
    # `release-tray.yml` asserts both outcomes on every build: an inverted
    # ordering here is silent, and produces an .app that launches the wrong
    # program or one that can never show a window.
    app = BUNDLE(  # noqa: F821
        tray_exe,
        coll,
        name="anaf-sync-tray.app",
        icon=None,
        bundle_identifier="ro.anaf-sync.tray",
        # PyInstaller writes this into the plist as CFBundleShortVersionString.
        version=__version__,
        info_plist={
            # Menu-bar-only: no Dock icon, no app-switcher entry.
            "LSUIElement": True,
            "NSHighResolutionCapable": True,
        },
    )
