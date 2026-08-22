"""Interval/time parsing and schedule mapping (no OS calls here)."""

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from anaf_sync import scheduling
from anaf_sync.scheduling import (
    Cadence,
    ScheduleError,
    _cadence_from_task_xml,
    _install_macos,
    _install_systemd,
    _launchd_cadence,
    _systemd_cadence,
    _windows_schedule,
    parse_daily_at,
    parse_interval,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("30m", dt.timedelta(minutes=30)),
        ("6h", dt.timedelta(hours=6)),
        ("1d", dt.timedelta(days=1)),
        (" 2H ", dt.timedelta(hours=2)),
    ],
)
def test_parse_interval(spec: str, expected: dt.timedelta) -> None:
    assert parse_interval(spec) == expected


@pytest.mark.parametrize("spec", ["", "5", "h", "5s", "1.5h", "-1h"])
def test_parse_interval_rejects_garbage(spec: str) -> None:
    with pytest.raises(ScheduleError):
        parse_interval(spec)


def test_parse_daily_at() -> None:
    assert parse_daily_at("08:30") == (8, 30)
    assert parse_daily_at("23:59") == (23, 59)
    with pytest.raises(ScheduleError):
        parse_daily_at("24:00")


@pytest.mark.parametrize(
    ("every", "expected"),
    [
        ("30m", ["/SC", "MINUTE", "/MO", "30"]),
        ("90m", ["/SC", "MINUTE", "/MO", "90"]),  # must not round to hourly
        ("6h", ["/SC", "MINUTE", "/MO", "360"]),
        ("24h", ["/SC", "DAILY", "/MO", "1"]),
        ("2d", ["/SC", "DAILY", "/MO", "2"]),
    ],
)
def test_windows_schedule_maps_intervals_exactly(
    every: str, expected: list[str]
) -> None:
    args, when = _windows_schedule(every, None)
    assert args == expected
    assert when == f"every {every}"


def test_windows_schedule_rejects_unrepresentable_intervals() -> None:
    with pytest.raises(ScheduleError, match="cannot run every 36h"):
        _windows_schedule("36h", None)


def test_windows_schedule_daily_at() -> None:
    args, when = _windows_schedule(None, "08:30")
    assert args == ["/SC", "DAILY", "/ST", "08:30"]
    assert when == "daily at 08:30"


# -- reading the cadence back -------------------------------------------------


@pytest.mark.parametrize(
    ("cadence", "described", "cron"),
    [
        (Cadence(every=dt.timedelta(minutes=30)), "every 30m", "*/30 * * * *"),
        (Cadence(every=dt.timedelta(hours=6)), "every 6h", "0 */6 * * *"),
        (Cadence(every=dt.timedelta(days=1)), "every 1d", "0 0 * * *"),
        (Cadence(daily_at=(8, 30)), "daily at 08:30", "30 8 * * *"),
        (Cadence(daily_at=(0, 0)), "daily at 00:00", "0 0 * * *"),
        # Cron's steps restart every hour and every day: these would drift.
        (Cadence(every=dt.timedelta(minutes=45)), "every 45m", None),
        (Cadence(every=dt.timedelta(minutes=90)), "every 90m", None),
        (Cadence(every=dt.timedelta(hours=5)), "every 5h", None),
        (Cadence(every=dt.timedelta(days=2)), "every 2d", None),
    ],
)
def test_cadence_describes_and_renders_cron(
    cadence: Cadence, described: str, cron: str | None
) -> None:
    assert cadence.describe() == described
    assert cadence.cron == cron


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"every": dt.timedelta(hours=1), "daily_at": (8, 0)}],
)
def test_cadence_is_an_interval_or_a_time_never_both(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Cadence(**kwargs)  # type: ignore[arg-type]


def _task_xml(trigger: str) -> bytes:
    """A schtasks ``/XML`` document, UTF-16 as the real tool emits it."""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        f"  <Triggers>\n{trigger}\n  </Triggers>\n"
        "  <Actions><Exec><Command>anaf-sync.exe</Command></Exec></Actions>\n"
        "</Task>\n"
    ).encode("utf-16")


_MINUTE_TRIGGER = """\
    <TimeTrigger>
      <Repetition>
        <Interval>PT30M</Interval>
        <Duration>P1D</Duration>
      </Repetition>
      <StartBoundary>2026-07-27T10:15:00</StartBoundary>
    </TimeTrigger>"""

_DAILY_TRIGGER = """\
    <CalendarTrigger>
      <StartBoundary>2026-07-27T08:30:00</StartBoundary>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>"""

_EVERY_TWO_DAYS_TRIGGER = """\
    <CalendarTrigger>
      <StartBoundary>2026-07-27T10:15:00</StartBoundary>
      <ScheduleByDay><DaysInterval>2</DaysInterval></ScheduleByDay>
    </CalendarTrigger>"""


@pytest.mark.parametrize(
    ("trigger", "expected"),
    [
        (_MINUTE_TRIGGER, Cadence(every=dt.timedelta(minutes=30))),
        (_DAILY_TRIGGER, Cadence(daily_at=(8, 30))),
        (_EVERY_TWO_DAYS_TRIGGER, Cadence(every=dt.timedelta(days=2))),
    ],
)
def test_cadence_from_task_xml(trigger: str, expected: Cadence) -> None:
    assert _cadence_from_task_xml(_task_xml(trigger)) == expected


@pytest.mark.parametrize(
    "raw",
    [
        b"ERROR: The system cannot find the file specified.",
        _task_xml("    <BootTrigger/>"),  # a shape we do not model
    ],
)
def test_cadence_from_task_xml_never_guesses(raw: bytes) -> None:
    assert _cadence_from_task_xml(raw) is None


@pytest.fixture
def quiet_os(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the systemctl/launchctl calls the installers make."""
    monkeypatch.setattr(scheduling, "_run", lambda cmd: None)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, "", "")
    )


@pytest.mark.parametrize(
    ("every", "daily_at", "expected"),
    [
        ("6h", None, Cadence(every=dt.timedelta(hours=6))),
        ("45m", None, Cadence(every=dt.timedelta(minutes=45))),
        (None, "07:30", Cadence(daily_at=(7, 30))),
    ],
)
def test_systemd_cadence_round_trips_what_install_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiet_os: None,
    every: str | None,
    daily_at: str | None,
    expected: Cadence,
) -> None:
    monkeypatch.setattr(scheduling, "_systemd_unit_dir", lambda: tmp_path)
    _install_systemd(Path("/usr/bin/anaf-sync"), every, daily_at)
    assert _systemd_cadence() == expected


@pytest.mark.parametrize(
    ("every", "daily_at", "expected"),
    [
        ("6h", None, Cadence(every=dt.timedelta(hours=6))),
        (None, "07:30", Cadence(daily_at=(7, 30))),
    ],
)
def test_launchd_cadence_round_trips_what_install_wrote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    quiet_os: None,
    every: str | None,
    daily_at: str | None,
    expected: Cadence,
) -> None:
    plist = tmp_path / "ro.anaf-sync.sync.plist"
    monkeypatch.setattr(scheduling, "_launchd_plist_path", lambda: plist)
    _install_macos(Path("/usr/local/bin/anaf-sync"), every, daily_at)
    assert _launchd_cadence() == expected


def test_cadence_readback_is_silent_when_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduling, "_systemd_unit_dir", lambda: tmp_path)
    monkeypatch.setattr(scheduling, "_launchd_plist_path", lambda: tmp_path / "none")
    assert _systemd_cadence() is None
    assert _launchd_cadence() is None


# -- console-script resolution ---------------------------------------------------


def _fake_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A sibling executable next to `sys.executable`, and a different one on PATH."""
    name = "anaf-sync.exe" if scheduling.sys.platform == "win32" else "anaf-sync"
    bundle, elsewhere = tmp_path / "bundle", tmp_path / "elsewhere"
    bundle.mkdir()
    elsewhere.mkdir()
    sibling, on_path = bundle / name, elsewhere / name
    sibling.touch()
    on_path.touch()
    monkeypatch.setattr(scheduling.sys, "executable", str(bundle / "anaf-sync-tray"))
    monkeypatch.setattr(scheduling.shutil, "which", lambda _n: str(on_path))
    return sibling, on_path


def test_resolve_script_prefers_path_when_not_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sibling, on_path = _fake_layout(tmp_path, monkeypatch)
    monkeypatch.delattr(scheduling.sys, "frozen", raising=False)
    assert scheduling.resolve_script("anaf-sync") == on_path.resolve()


def test_resolve_script_prefers_its_own_bundle_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen bundle is self-contained: a stale pip install must not win.

    Otherwise an operator with both would get the bundle's tray driving some
    other version's CLI — against the same archive DB.
    """
    sibling, _on_path = _fake_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduling.sys, "frozen", True, raising=False)
    assert scheduling.resolve_script("anaf-sync") == sibling.resolve()


def test_resolve_script_falls_back_to_the_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sibling, _on_path = _fake_layout(tmp_path, monkeypatch)
    monkeypatch.delattr(scheduling.sys, "frozen", raising=False)
    monkeypatch.setattr(scheduling.shutil, "which", lambda _n: None)
    assert scheduling.resolve_script("anaf-sync") == sibling.resolve()


def test_resolve_script_gives_up_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduling.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(scheduling.shutil, "which", lambda _n: None)
    monkeypatch.delattr(scheduling.sys, "frozen", raising=False)
    assert scheduling.resolve_script("anaf-sync") is None
    with pytest.raises(ScheduleError, match="cannot locate"):
        scheduling.sync_executable()
