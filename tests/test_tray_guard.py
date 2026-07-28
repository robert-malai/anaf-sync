"""The package entry-point guard — runs with or without the tray extra."""

import subprocess
import sys
import types
from typing import Any

import pytest

import anaf_sync.tray as tray


class _Stream:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _fake_pyside6(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "PySide6", types.ModuleType("PySide6"))


def test_main_prints_install_hint_without_pyside6(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Force `import PySide6` to fail even if the extra happens to be installed.
    monkeypatch.setitem(sys.modules, "PySide6", None)

    assert tray.main() == 1
    assert 'pip install "anaf-sync[tray]"' in capsys.readouterr().err


def test_main_detaches_when_a_terminal_is_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_pyside6(monkeypatch)
    monkeypatch.setattr(tray, "_holds_a_terminal", lambda: True)
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/anaf-sync-tray"])
    spawned: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda cmd, **kwargs: spawned.append((cmd, kwargs))
    )

    assert tray.main() == 0
    command, kwargs = spawned[0]
    assert command == ["/usr/local/bin/anaf-sync-tray", "--foreground"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_main_runs_attached_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_pyside6(monkeypatch)
    monkeypatch.setattr(tray, "_holds_a_terminal", lambda: False)
    app_stub = types.ModuleType("anaf_sync.tray.app")
    app_stub.run = lambda: 42  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anaf_sync.tray.app", app_stub)

    assert tray.main() == 42


def test_holds_a_terminal_on_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["anaf-sync-tray"])
    monkeypatch.setattr(sys, "stdin", _Stream(tty=True))
    monkeypatch.setattr(sys, "stdout", _Stream(tty=False))
    monkeypatch.setattr(sys, "stderr", _Stream(tty=False))

    assert tray._holds_a_terminal()


def test_holds_no_terminal_in_a_manager_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # launchd / XDG autostart: stdio exists but none of it is a tty.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["anaf-sync-tray"])
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, _Stream(tty=False))

    assert not tray._holds_a_terminal()


def test_foreground_flag_disables_detaching(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", ["anaf-sync-tray", "--foreground"])
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, _Stream(tty=True))

    assert not tray._holds_a_terminal()


def test_windows_never_detaches(monkeypatch: pytest.MonkeyPatch) -> None:
    # The gui-script launcher already frees the shell; detaching is POSIX-only.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "argv", ["anaf-sync-tray"])
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, _Stream(tty=True))

    assert not tray._holds_a_terminal()
