"""The pure core→UI status bridge (no Qt)."""

import datetime as dt
from pathlib import Path

from anaf_sync.config import write_default_config
from anaf_sync.state import Archive, CatalogEntry, RunRecord
from anaf_sync.tray import status as tray_status
from anaf_sync.tray.status import load_status

_NOW = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.UTC)


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    write_default_config(path)
    return path


def _state(tmp_path: Path) -> Path:
    return tmp_path / "state.db"


def test_ok_state_with_valid_config_and_recent_run(tmp_path: Path) -> None:
    with Archive.open(_state(tmp_path)) as archive:
        archive.record(
            CatalogEntry(
                message_id="m1",
                cif="111",
                direction="received",
                base_path="p",
                artifacts=["zip"],
            )
        )
        archive.record_run(
            RunRecord(
                finished_at=_NOW - dt.timedelta(hours=2), outcome="ok", archived=3
            )
        )

    status = load_status(
        state_path=_state(tmp_path), config_path=_config(tmp_path), now=_NOW
    )
    assert status.state == "ok"
    assert status.headline == tray_status.ARCHIVE_UP_TO_DATE
    assert status.alert_text is None
    assert status.archived_count == 1
    assert status.output_dir is not None
    assert "3 facturi noi" in status.subline


def test_warn_state_on_failure(tmp_path: Path) -> None:
    with Archive.open(_state(tmp_path)) as archive:
        archive.record_failure("m9", "HTTP 500")

    status = load_status(
        state_path=_state(tmp_path), config_path=_config(tmp_path), now=_NOW
    )
    assert status.state == "warn"
    assert status.headline == tray_status.NEEDS_ATTENTION
    assert status.alert_text is not None
    assert "eșuează repetat" in status.alert_text
    assert status.alert_state == "warn"
    assert status.alert_command is None


def test_err_state_on_auth_failure_shows_command_chip(tmp_path: Path) -> None:
    with Archive.open(_state(tmp_path)) as archive:
        archive.record_run(
            RunRecord(
                finished_at=_NOW - dt.timedelta(days=1, hours=2),
                outcome="failed",
                error="token expired",
                error_kind="AnafAuthError",
            )
        )

    status = load_status(
        state_path=_state(tmp_path), config_path=_config(tmp_path), now=_NOW
    )
    assert status.state == "err"
    assert status.headline == tray_status.SYNC_BROKEN
    assert status.alert_command == "anafpy auth login"
    assert status.alert_state == "err"
    assert status.subline.startswith("Ultima sincronizare reușită")


def test_err_state_on_crash_uses_generic_alert(tmp_path: Path) -> None:
    with Archive.open(_state(tmp_path)) as archive:
        archive.record_run(RunRecord(finished_at=_NOW, outcome="crashed"))

    status = load_status(
        state_path=_state(tmp_path), config_path=_config(tmp_path), now=_NOW
    )
    assert status.state == "err"
    assert status.alert_command is None
    assert status.alert_text is not None


def test_broken_config_is_err_not_a_crash(tmp_path: Path) -> None:
    bad = tmp_path / "config.toml"
    bad.write_text("cif = ", encoding="utf-8")  # truncated TOML

    status = load_status(state_path=_state(tmp_path), config_path=bad, now=_NOW)
    assert status.state == "err"
    assert status.output_dir is None  # unknown when config is unreadable
    assert status.alert_text is not None


def test_missing_state_and_config_is_tolerated(tmp_path: Path) -> None:
    # No sync has ever run and no config exists: err (config), never an exception.
    status = load_status(
        state_path=tmp_path / "absent.db",
        config_path=tmp_path / "absent.toml",
        now=_NOW,
    )
    assert status.state == "err"
    assert status.archived_count == 0
    assert status.subline == tray_status.NEVER_SYNCED


def test_no_run_yet_with_valid_config_is_ok(tmp_path: Path) -> None:
    status = load_status(
        state_path=tmp_path / "absent.db", config_path=_config(tmp_path), now=_NOW
    )
    assert status.state == "ok"
    assert status.subline == tray_status.NEVER_SYNCED


# -- phrase builders (moved from the former strings module) ---------------------


def test_new_invoices_phrase_agrees_in_number() -> None:
    assert tray_status._new_invoices_phrase(1) == "1 factură nouă"
    assert tray_status._new_invoices_phrase(3) == "3 facturi noi"
    assert tray_status._new_invoices_phrase(21) == "21 de facturi noi"


def test_failing_alert_matches_handoff() -> None:
    alert = tray_status._failing_alert(1, "TERMOENERGIA S.R.L.", 9)
    assert (
        alert
        == "1 factură eșuează repetat — TERMOENERGIA S.R.L. — expiră din SPV în 9 zile"
    )


def test_failing_alert_without_partner_or_expired() -> None:
    assert (
        tray_status._failing_alert(2, None, 0)
        == "2 facturi eșuează repetat — a expirat din SPV"
    )


def test_auth_alert_keeps_command_untranslated() -> None:
    assert tray_status.AUTH_LOGIN_COMMAND == "anafpy auth login"
    assert tray_status.AUTH_EXPIRED_PREFIX.startswith("Autentificarea ANAF a expirat")
