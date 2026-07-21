"""CLI boundary behaviour: exit codes and error formatting, no network."""

from pathlib import Path

import pytest

from anaf_sync import cli
from anaf_sync.config import write_default_config
from anaf_sync.engine import SyncReport
from anaf_sync.state import Archive


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the real state dir and OS scheduler."""
    monkeypatch.setattr(cli, "default_state_path", lambda: tmp_path / "state.db")
    monkeypatch.setattr(cli, "schedule_status", lambda: "not installed")


class _DummyAuth:
    """Stands in for AuthSettings so `sync` never touches real credentials."""

    def build_provider(self) -> object:
        return object()


def _fake_sync(report: SyncReport) -> object:
    async def run(*args: object, **kwargs: object) -> SyncReport:
        return report

    return run


def _last_run(tmp_path: Path) -> object:
    return Archive.open_readonly(tmp_path / "state.db").last_run()


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
    # The boundary error is captured in the last-run record with its kind.
    run = _last_run(tmp_path)
    assert run is not None
    assert run.outcome == "failed"
    assert run.error_kind == "FileNotFoundError"


async def test_sync_records_ok_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config)
    monkeypatch.setattr(
        cli.AuthSettings, "from_env", staticmethod(lambda: _DummyAuth())
    )
    monkeypatch.setattr(cli, "run_sync", _fake_sync(SyncReport(listed=5, downloaded=2)))

    result = await cli.sync(config=config)

    assert result == 0
    run = _last_run(tmp_path)
    assert run is not None
    assert run.outcome == "ok"
    assert run.listed == 5
    assert run.archived == 2
    assert run.error is None


async def test_sync_records_failed_run_on_message_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config)
    monkeypatch.setattr(
        cli.AuthSettings, "from_env", staticmethod(lambda: _DummyAuth())
    )
    report = SyncReport(listed=3, downloaded=2, failures=[("m1", "HTTP 500")])
    monkeypatch.setattr(cli, "run_sync", _fake_sync(report))

    result = await cli.sync(config=config)

    assert result == 1  # non-zero exit when downloads failed
    run = _last_run(tmp_path)
    assert run is not None
    assert run.outcome == "failed"
    assert run.failures == 1
    assert run.error_kind is None  # per-message failures are not an auth/config break


async def test_dry_run_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config)
    monkeypatch.setattr(
        cli.AuthSettings, "from_env", staticmethod(lambda: _DummyAuth())
    )
    monkeypatch.setattr(cli, "run_sync", _fake_sync(SyncReport(would_download=4)))

    result = await cli.sync(config=config, dry_run=True)

    assert result == 0
    # A dry run touches no state, so no last-run record is written.
    assert _last_run(tmp_path) is None


def test_tray_status_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "autostart_status", lambda: "not enabled")
    assert cli.tray_status_cmd() == 0
    assert "not enabled" in capsys.readouterr().out


def test_tray_install_reports_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from anaf_sync.autostart import AutostartError

    def boom() -> str:
        raise AutostartError("cannot locate the `anaf-sync-tray` executable")

    monkeypatch.setattr(cli, "autostart_install", boom)
    assert cli.tray_install_cmd() == 1
    assert "anaf-sync-tray" in capsys.readouterr().err


def test_log_crash_records_crashed_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        raise RuntimeError("kaboom")
    except RuntimeError as exc:
        cli._log_crash(type(exc), exc, exc.__traceback__)

    run = _last_run(tmp_path)
    assert run is not None
    assert run.outcome == "crashed"
    assert run.error_kind == "RuntimeError"
    assert "kaboom" in (run.error or "")
