"""Register the tray companion to launch at login — the platform's way, not ours.

Mirrors :mod:`anaf_sync.scheduling`: a macOS LaunchAgent, a Windows ``Run``
registry value, and an XDG autostart ``.desktop`` file. The payload builders are
pure (they return strings/dicts) so they can be unit-tested without touching the
real system; only :func:`install` / :func:`remove` / :func:`status` perform the
platform calls, behind the same seam ``scheduling`` uses.
"""

from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = [
    "AutostartError",
    "install",
    "linux_desktop_entry",
    "macos_plist",
    "remove",
    "status",
    "windows_run_command",
]

_LABEL = "ro.anaf-sync.tray"
_RUN_VALUE_NAME = "anaf-sync-tray"
_RUN_HKCU = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartError(RuntimeError):
    """Enabling or disabling tray autostart failed."""


def tray_command() -> list[str]:
    """The command that launches the tray, resolved for a login-time context.

    Frozen (PyInstaller) builds are their own executable; otherwise resolve the
    ``anaf-sync-tray`` console script the way :mod:`scheduling` resolves
    ``anaf-sync`` — so autostart works without any venv activation.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable]
    found = shutil.which("anaf-sync-tray")
    if found:
        return [str(Path(found).resolve())]
    candidate = Path(sys.executable).with_name(
        "anaf-sync-tray.exe" if sys.platform == "win32" else "anaf-sync-tray"
    )
    if candidate.exists():
        return [str(candidate.resolve())]
    raise AutostartError(
        "cannot locate the `anaf-sync-tray` executable — install the tray extra "
        '(`pip install "anaf-sync[tray]"`) so the script is on PATH'
    )


# -- pure payload builders (unit-tested) -----------------------------------------


def macos_plist(command: list[str]) -> dict[str, object]:
    """The LaunchAgent plist body: run at load, interactive, not kept alive."""
    return {
        "Label": _LABEL,
        "ProgramArguments": command,
        "RunAtLoad": True,
        "ProcessType": "Interactive",
    }


def linux_desktop_entry(command: list[str]) -> str:
    """The XDG autostart ``.desktop`` file body."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=anaf-sync\n"
        "Comment=Arhivare e-Factura în bara de sistem\n"
        f"Exec={shlex.join(command)}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Terminal=false\n"
    )


def windows_run_command(command: list[str]) -> str:
    """The string stored under the ``Run`` registry value."""
    return subprocess.list2cmdline(command)


# -- platform paths --------------------------------------------------------------


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def _linux_desktop_path() -> Path:
    return Path.home() / ".config" / "autostart" / "anaf-sync-tray.desktop"


# -- install / remove / status ---------------------------------------------------


def install() -> str:
    """Enable tray autostart (idempotent); returns a human summary."""
    command = tray_command()
    if sys.platform == "darwin":
        return _install_macos(command)
    if sys.platform == "win32":
        return _install_windows(command)
    return _install_linux(command)


def remove() -> str:
    if sys.platform == "darwin":
        return _remove_macos()
    if sys.platform == "win32":
        return _remove_windows()
    return _remove_linux()


def status() -> str:
    if sys.platform == "darwin":
        return "enabled" if _macos_plist_path().exists() else "not enabled"
    if sys.platform == "win32":
        return "enabled" if _windows_value() is not None else "not enabled"
    return "enabled" if _linux_desktop_path().exists() else "not enabled"


# -- macOS -----------------------------------------------------------------------


def _install_macos(command: list[str]) -> str:
    path = _macos_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    path.write_bytes(plistlib.dumps(macos_plist(command)))
    subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
    return f"tray autostart enabled — LaunchAgent {_LABEL!r}"


def _remove_macos() -> str:
    path = _macos_plist_path()
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    path.unlink(missing_ok=True)
    return f"tray autostart removed — LaunchAgent {_LABEL!r}"


# -- Linux -----------------------------------------------------------------------


def _install_linux(command: list[str]) -> str:
    path = _linux_desktop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(linux_desktop_entry(command), encoding="utf-8")
    return f"tray autostart enabled — {path}"


def _remove_linux() -> str:
    _linux_desktop_path().unlink(missing_ok=True)
    return "tray autostart removed — ~/.config/autostart/anaf-sync-tray.desktop"


# -- Windows ---------------------------------------------------------------------


def _install_windows(command: list[str]) -> str:
    # `reg`, like scheduling.py's `schtasks`, keeps the OS call behind one seam
    # and sidesteps `winreg`'s Windows-only typing on the dev machine.
    _run_reg(
        [
            "reg",
            "add",
            _RUN_HKCU,
            "/v",
            _RUN_VALUE_NAME,
            "/t",
            "REG_SZ",
            "/d",
            windows_run_command(command),
            "/f",
        ]
    )
    return f"tray autostart enabled — HKCU Run value {_RUN_VALUE_NAME!r}"


def _remove_windows() -> str:
    subprocess.run(
        ["reg", "delete", _RUN_HKCU, "/v", _RUN_VALUE_NAME, "/f"],
        capture_output=True,
        text=True,
    )
    return f"tray autostart removed — HKCU Run value {_RUN_VALUE_NAME!r}"


def _windows_value() -> str | None:
    result = subprocess.run(
        ["reg", "query", _RUN_HKCU, "/v", _RUN_VALUE_NAME],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def _run_reg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise AutostartError(f"reg failed: {detail}")
