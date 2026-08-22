"""Autostart payload builders + executable resolution (no OS calls)."""

import sys
from pathlib import Path

import pytest

from anaf_sync import scheduling
from anaf_sync.autostart import (
    AutostartError,
    linux_desktop_entry,
    macos_plist,
    tray_command,
    windows_run_command,
)


def test_macos_plist_is_interactive_run_at_load_no_keepalive() -> None:
    plist = macos_plist(["/opt/anaf-sync-tray"])
    assert plist["Label"] == "ro.anaf-sync.tray"
    assert plist["ProgramArguments"] == ["/opt/anaf-sync-tray"]
    assert plist["RunAtLoad"] is True
    assert plist["ProcessType"] == "Interactive"
    assert "KeepAlive" not in plist  # a tray must not be relaunched forever


def test_linux_desktop_entry_body() -> None:
    entry = linux_desktop_entry(["/usr/bin/anaf-sync-tray"])
    assert entry.startswith("[Desktop Entry]")
    assert "Exec=/usr/bin/anaf-sync-tray" in entry
    assert "X-GNOME-Autostart-enabled=true" in entry


def test_linux_desktop_entry_quotes_spaced_paths() -> None:
    entry = linux_desktop_entry(["/opt/My Apps/anaf-sync-tray"])
    assert "Exec='/opt/My Apps/anaf-sync-tray'" in entry


def test_windows_run_command_quotes() -> None:
    assert windows_run_command([r"C:\Program Files\anaf-sync-tray.exe"]) == (
        '"C:\\Program Files\\anaf-sync-tray.exe"'
    )


def test_tray_command_picks_the_tray_out_of_a_frozen_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never `sys.executable`: inside a bundle, that is the CLI.

    `anaf-sync tray install` is a CLI subcommand, so when the Windows installer
    ticks the autostart box it is `anaf-sync.exe` that runs it. Registering
    `sys.executable` would put a console program in the Run key.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    suffix = ".exe" if sys.platform == "win32" else ""
    tray = tmp_path / f"anaf-sync-tray{suffix}"
    tray.touch()
    monkeypatch.setattr(sys, "executable", str(tmp_path / f"anaf-sync{suffix}"))
    monkeypatch.setattr(scheduling.shutil, "which", lambda _name: None)
    assert tray_command() == [str(tray.resolve())]


def test_tray_command_resolves_console_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    script = tmp_path / "anaf-sync-tray"
    script.write_text("#!/bin/sh\n")
    # The resolver is shared with scheduling.py, so the seam lives there.
    monkeypatch.setattr(scheduling.shutil, "which", lambda _name: str(script))
    assert tray_command() == [str(script.resolve())]


def test_tray_command_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(scheduling.shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    with pytest.raises(AutostartError, match="anaf-sync-tray"):
        tray_command()
