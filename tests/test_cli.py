"""CLI boundary behaviour: exit codes and error formatting, no network."""

from pathlib import Path

import pytest

from anaf_sync import cli
from anaf_sync.config import load_config, write_default_config
from anaf_sync.engine import RepairReport, SyncReport
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


def test_init_bakes_the_cif_into_the_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"

    assert cli.init(["RO12345678"], config=path) == 0

    assert load_config(path).cifs == ["12345678"]
    assert str(path) in capsys.readouterr().out


def test_init_rejects_a_bad_cif(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"

    assert cli.init(["not-a-cif"], config=path) == 1

    assert "not numeric" in capsys.readouterr().err
    assert not path.exists()


def test_init_refuses_to_overwrite_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    assert cli.init(["12345678"], config=path) == 0

    assert cli.init(["87654321"], config=path) == 1
    assert "--force" in capsys.readouterr().err
    assert load_config(path).cifs == ["12345678"]  # left untouched

    assert cli.init(["87654321"], config=path, force=True) == 0
    assert load_config(path).cifs == ["87654321"]


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
    write_default_config(config, cifs=["12345678"])
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
    write_default_config(config, cifs=["12345678"])
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
    write_default_config(config, cifs=["12345678"])
    monkeypatch.setattr(
        cli.AuthSettings, "from_env", staticmethod(lambda: _DummyAuth())
    )
    monkeypatch.setattr(cli, "run_sync", _fake_sync(SyncReport(would_download=4)))

    result = await cli.sync(config=config, dry_run=True)

    assert result == 0
    # A dry run touches no state, so no last-run record is written.
    assert _last_run(tmp_path) is None


async def test_sync_prints_a_repair_line_only_when_there_was_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config, cifs=["12345678"])
    monkeypatch.setattr(
        cli.AuthSettings, "from_env", staticmethod(lambda: _DummyAuth())
    )
    report = SyncReport(listed=1, repair=RepairReport(candidates=2, rendered=2))
    monkeypatch.setattr(cli, "run_sync", _fake_sync(report))

    assert await cli.sync(config=config) == 0
    assert "pdf repair: rendered 2" in capsys.readouterr().out

    quiet = SyncReport(listed=1, repair=RepairReport())
    monkeypatch.setattr(cli, "run_sync", _fake_sync(quiet))
    assert await cli.sync(config=config) == 0
    assert "pdf repair" not in capsys.readouterr().out


def _fake_repair(report: RepairReport) -> object:
    async def run(*args: object, **kwargs: object) -> RepairReport:
        return report

    return run


async def test_render_reports_and_leaves_last_run_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config, cifs=["12345678"])
    report = RepairReport(candidates=2, rendered=1, refused=1)
    monkeypatch.setattr(cli, "run_repair", _fake_repair(report))

    result = await cli.render(config=config)

    assert result == 0  # a refusal is retryable, not an operator error
    assert "rendered 1 | refused 1" in capsys.readouterr().out
    # `last_run` is the schedule's health record; a manual repair is not a sync.
    assert _last_run(tmp_path) is None


async def test_render_exits_nonzero_on_transport_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config, cifs=["12345678"])
    report = RepairReport(candidates=1, failures=[("m1", "bad gateway")])
    monkeypatch.setattr(cli, "run_repair", _fake_repair(report))

    assert await cli.render(config=config) == 1
    assert "failed m1: bad gateway" in capsys.readouterr().err


async def test_render_requires_pdf_in_the_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config, cifs=["12345678"])
    config.write_text(
        config.read_text(encoding="utf-8").replace('["zip", "pdf"]', '["zip"]'),
        encoding="utf-8",
    )

    assert await cli.render(config=config) == 1
    assert "output.artifacts" in capsys.readouterr().err


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
