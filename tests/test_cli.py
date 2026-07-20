"""CLI boundary behaviour: exit codes and error formatting, no network."""

from pathlib import Path

import pytest

from anaf_sync import cli


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the real state dir and OS scheduler."""
    monkeypatch.setattr(cli, "default_state_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(cli, "schedule_status", lambda: "not installed")


def test_status_survives_a_corrupt_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "config.toml"
    bad.write_text("cif = ", encoding="utf-8")  # truncated TOML

    assert cli.status(config=bad) == 0

    out = capsys.readouterr().out
    assert "INVALID" in out
    assert "schedule:" in out  # the rest of the report still printed


async def test_sync_without_config_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = await cli.sync(config=tmp_path / "absent.toml")

    assert result == 1
    assert "anaf-sync init" in capsys.readouterr().err
