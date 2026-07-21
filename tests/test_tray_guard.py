"""The package entry-point guard — runs with or without the tray extra."""

import sys

import pytest

import anaf_sync.tray as tray


def test_main_prints_install_hint_without_pyside6(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Force `import PySide6` to fail even if the extra happens to be installed.
    monkeypatch.setitem(sys.modules, "PySide6", None)

    assert tray.main() == 1
    assert 'pip install "anaf-sync[tray]"' in capsys.readouterr().err
