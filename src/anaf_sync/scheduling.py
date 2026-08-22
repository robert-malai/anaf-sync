"""Register anaf-sync with the OS scheduler — no daemon of our own.

Windows uses Task Scheduler (``schtasks``), Linux a systemd *user* timer, and
macOS a launchd agent. Each backend runs ``anaf-sync sync`` with the resolved
console-script path, so the job works regardless of how the venv is activated.

:func:`status` reads the cadence back *from the scheduler* rather than from a
record of our own — the OS holds the truth, and a second copy would be one more
thing to drift.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import plistlib
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

__all__ = [
    "Cadence",
    "ScheduleError",
    "install",
    "installed_cadence",
    "resolve_script",
    "run_checked",
    "status",
    "sync_executable",
    "uninstall",
]

_TASK_NAME = "AnafSync"  # Windows task / systemd unit / launchd label stem
_LAUNCHD_LABEL = "ro.anaf-sync.sync"
_UNIT_NAME = "anaf-sync"  # systemd user unit stem

_INTERVAL_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# Reading a cadence back: Task Scheduler's XML namespace and duration format,
# and the two systemd timer directives `_install_systemd` writes.
_TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
_DURATION_RE = re.compile(
    r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", re.IGNORECASE
)
_ON_ACTIVE_RE = re.compile(r"^OnUnitActiveSec=(\d+)s?$", re.MULTILINE)
_ON_CALENDAR_RE = re.compile(
    r"^OnCalendar=\*-\*-\* (\d{2}):(\d{2}):\d{2}$", re.MULTILINE
)


class ScheduleError(RuntimeError):
    """Installing or removing the scheduled job failed."""


@dataclasses.dataclass(frozen=True)
class Cadence:
    """How often the installed job runs, as read back from the scheduler.

    Exactly one of the two fields is set, mirroring ``schedule install``'s
    ``--every`` / ``--daily-at``.
    """

    every: dt.timedelta | None = None
    daily_at: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if (self.every is None) == (self.daily_at is None):
            raise ValueError("a cadence is either an interval or a daily time")

    def describe(self) -> str:
        """The cadence in the vocabulary of the flags that installed it."""
        if self.daily_at is not None:
            hour, minute = self.daily_at
            return f"daily at {hour:02d}:{minute:02d}"
        assert self.every is not None
        return f"every {format_interval(self.every)}"

    @property
    def cron(self) -> str | None:
        """The equivalent five-field cron expression, or ``None``.

        ``None`` whenever cron cannot express the cadence *exactly*: its step
        syntax restarts each hour and each day, so only divisors of 60 minutes
        and of 24 hours survive — ``every 45m`` and ``every 2d`` do not, and a
        familiar-but-wrong expression is worse than none.

        The expression describes the *cadence*, not the anchor: an interval job
        counts from when it was installed, so its real firing times are
        generally offset from cron's wall-clock grid.
        """
        if self.daily_at is not None:
            hour, minute = self.daily_at
            return f"{minute} {hour} * * *"
        assert self.every is not None
        seconds = int(self.every.total_seconds())
        if seconds % 60:
            return None
        minutes = seconds // 60
        if minutes < 60:
            return f"*/{minutes} * * * *" if 60 % minutes == 0 else None
        if minutes % 60:
            return None
        hours = minutes // 60
        if hours == 24:
            return "0 0 * * *"
        return f"0 */{hours} * * *" if hours < 24 and 24 % hours == 0 else None


def format_interval(delta: dt.timedelta) -> str:
    """Render a timedelta the way ``--every`` spells it (``30m``, ``6h``, ``2d``)."""
    seconds = int(delta.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def parse_interval(value: str) -> dt.timedelta:
    """``"30m"`` / ``"6h"`` / ``"1d"`` → timedelta.

    Raises:
        ScheduleError: the spec is unparseable or under one minute.
    """
    match = _INTERVAL_RE.match(value.strip())
    if match is None:
        raise ScheduleError(
            f"cannot parse interval {value!r} — use forms like 30m, 6h, 1d"
        )
    amount, unit = int(match.group(1)), match.group(2).lower()
    delta = {
        "m": dt.timedelta(minutes=amount),
        "h": dt.timedelta(hours=amount),
        "d": dt.timedelta(days=amount),
    }[unit]
    if delta < dt.timedelta(minutes=1):
        raise ScheduleError("the interval must be at least one minute")
    return delta


def parse_daily_at(value: str) -> tuple[int, int]:
    match = _TIME_RE.match(value.strip())
    if match is None:
        raise ScheduleError(f"cannot parse time {value!r} — use HH:MM (24h)")
    return int(match.group(1)), int(match.group(2))


def resolve_script(name: str) -> Path | None:
    """Resolve a console script for use outside this shell, or ``None``.

    Two places to look: next to the current executable, and PATH. Which comes
    first depends on whether we are frozen.

    A PyInstaller bundle ships both executables in one directory and is meant
    to be self-contained, so there the sibling wins: an operator who also has
    an older ``pip install`` on PATH must not get the tray of one version
    driving the CLI of another. Unfrozen, PATH wins as before, and the sibling
    (the venv's ``bin``/``Scripts``) is the fallback that makes the resolution
    work without any venv activation.

    Shared with :mod:`anaf_sync.autostart`, which resolves ``anaf-sync-tray``
    the same way.
    """
    sibling = Path(sys.executable).with_name(
        f"{name}.exe" if sys.platform == "win32" else name
    )
    if getattr(sys, "frozen", False) and sibling.exists():
        return sibling.resolve()
    if found := shutil.which(name):
        return Path(found).resolve()
    if sibling.exists():
        return sibling.resolve()
    return None


def sync_executable() -> Path:
    """The anaf-sync console script; the schedulers and the tray both run it."""
    script = resolve_script("anaf-sync")
    if script is None:
        raise ScheduleError(
            "cannot locate the `anaf-sync` executable — install the package "
            "(e.g. `uv tool install anaf-sync`) so the script is on PATH"
        )
    return script


def run_checked(
    cmd: list[str], *, error: type[RuntimeError]
) -> subprocess.CompletedProcess[str]:
    """Run an OS tool; raise ``error`` carrying the tool's own message."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise error(f"{' '.join(cmd[:2])} failed: {detail}")
    return result


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return run_checked(cmd, error=ScheduleError)


def install(*, every: str | None, daily_at: str | None) -> str:
    """Install (or replace) the scheduled job; returns a human summary.

    Exactly one of ``every`` (interval) or ``daily_at`` (HH:MM) must be given.
    """
    if (every is None) == (daily_at is None):
        raise ScheduleError("pass exactly one of --every or --daily-at")
    exe = sync_executable()
    if sys.platform == "win32":
        return _install_windows(exe, every, daily_at)
    if sys.platform == "darwin":
        return _install_macos(exe, every, daily_at)
    return _install_systemd(exe, every, daily_at)


def uninstall() -> str:
    if sys.platform == "win32":
        _run(["schtasks", "/Delete", "/TN", _TASK_NAME, "/F"])
        return f"removed Task Scheduler task {_TASK_NAME!r}"
    if sys.platform == "darwin":
        plist = _launchd_plist_path()
        subprocess.run(
            ["launchctl", "unload", str(plist)], capture_output=True, text=True
        )
        plist.unlink(missing_ok=True)
        return f"removed launchd agent {_LAUNCHD_LABEL!r}"
    _run(["systemctl", "--user", "disable", "--now", f"{_UNIT_NAME}.timer"])
    for suffix in (".timer", ".service"):
        (_systemd_unit_dir() / f"{_UNIT_NAME}{suffix}").unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    return f"removed systemd user timer {_UNIT_NAME!r}"


def installed_cadence() -> Cadence | None:
    """The cadence the OS scheduler currently holds, or ``None`` if unreadable.

    ``None`` covers both "nothing is installed" and "installed, but the job was
    hand-edited into a shape we do not model" — a diagnostic must never guess.
    """
    if sys.platform == "win32":
        return _windows_cadence()
    if sys.platform == "darwin":
        return _launchd_cadence()
    return _systemd_cadence()


def _cadence_suffix(cadence: Cadence | None) -> str:
    """`` — runs every 6h (cron: 0 */6 * * *)`` for a status line; ``""`` if unknown."""
    if cadence is None:
        return ""
    cron = cadence.cron
    return f" — runs {cadence.describe()}" + (f" (cron: {cron})" if cron else "")


def status() -> str:
    """The installed state and cadence, as ``status`` and the tray print it.

    The exact string ``"not installed"`` is the contract both callers test
    against — keep it verbatim.
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK_NAME],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return "not installed"
        suffix = _cadence_suffix(_windows_cadence())
        return f"Task Scheduler task {_TASK_NAME!r}: installed{suffix}"
    if sys.platform == "darwin":
        if not _launchd_plist_path().exists():
            return "not installed"
        result = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL], capture_output=True, text=True
        )
        loaded = "loaded" if result.returncode == 0 else "installed but not loaded"
        suffix = _cadence_suffix(_launchd_cadence())
        return f"launchd agent {_LAUNCHD_LABEL}: {loaded}{suffix}"
    result = subprocess.run(
        ["systemctl", "--user", "list-timers", f"{_UNIT_NAME}.timer", "--all"],
        capture_output=True,
        text=True,
    )
    out = result.stdout.strip()
    if _UNIT_NAME not in out:
        return "not installed"
    # The timer table carries the next elapse, which no other backend reports —
    # worth keeping under our own summary line.
    suffix = _cadence_suffix(_systemd_cadence())
    return f"systemd user timer {_UNIT_NAME!r}: installed{suffix}\n{out}"


# -- Windows ---------------------------------------------------------------------


def _windows_schedule(every: str | None, daily_at: str | None) -> tuple[list[str], str]:
    """The ``/SC``-family schtasks arguments for the requested cadence.

    Exact or rejected — never silently rounded: sub-day intervals map to
    ``/SC MINUTE`` (``/MO`` caps at 1439), whole days to ``/SC DAILY``.

    Raises:
        ScheduleError: the interval is over a day but not a whole number of
            days — Task Scheduler has no modifier that runs it exactly.
    """
    if every is not None:
        minutes = int(parse_interval(every).total_seconds() // 60)
        if minutes % 1440 == 0:
            return ["/SC", "DAILY", "/MO", str(minutes // 1440)], f"every {every}"
        if minutes <= 1439:
            return ["/SC", "MINUTE", "/MO", str(minutes)], f"every {every}"
        raise ScheduleError(
            f"Task Scheduler cannot run every {every} exactly — use an "
            "interval up to 24h or a whole number of days (e.g. 2d)"
        )
    assert daily_at is not None
    hour, minute = parse_daily_at(daily_at)
    when = f"daily at {hour:02d}:{minute:02d}"
    return ["/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}"], when


def _install_windows(exe: Path, every: str | None, daily_at: str | None) -> str:
    schedule, when = _windows_schedule(every, daily_at)
    command = f'"{exe}" sync'
    args = ["schtasks", "/Create", "/F", "/TN", _TASK_NAME, "/TR", command]
    _run(args + schedule)
    return (
        f"Task Scheduler task {_TASK_NAME!r} installed — runs {when} "
        "(only while you are logged on)"
    )


def _parse_duration(value: str) -> dt.timedelta | None:
    """ISO 8601 duration (``PT30M``) → timedelta; ``None`` if unrecognised."""
    match = _DURATION_RE.match(value.strip())
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    delta = dt.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return delta or None


def _cadence_from_task_xml(raw: bytes) -> Cadence | None:
    """Read the cadence out of ``schtasks /XML`` output.

    XML rather than ``/FO LIST``: the list format is localised, so a Romanian
    Windows prints its own field names. ElementTree is handed the raw bytes on
    purpose — schtasks emits UTF-16, and only the XML declaration says so.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    interval = root.find(f".//{_TASK_NS}Repetition/{_TASK_NS}Interval")
    if interval is not None and (delta := _parse_duration(interval.text or "")):
        return Cadence(every=delta)
    # No repetition: a ``/SC DAILY`` task. ``/MO n`` above 1 is an n-day
    # interval; a plain daily task is pinned to its start time.
    days = root.find(f".//{_TASK_NS}ScheduleByDay/{_TASK_NS}DaysInterval")
    if days is None:
        return None
    count = int(days.text) if days.text and days.text.isdigit() else 1
    if count > 1:
        return Cadence(every=dt.timedelta(days=count))
    start = root.find(f".//{_TASK_NS}CalendarTrigger/{_TASK_NS}StartBoundary")
    if start is None or not start.text:
        return None
    try:
        moment = dt.datetime.fromisoformat(start.text.strip())
    except ValueError:
        return None
    return Cadence(daily_at=(moment.hour, moment.minute))


def _windows_cadence() -> Cadence | None:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", _TASK_NAME, "/XML", "ONE"], capture_output=True
    )
    return None if result.returncode != 0 else _cadence_from_task_xml(result.stdout)


# -- Linux (systemd user units) ---------------------------------------------------


def _systemd_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _install_systemd(exe: Path, every: str | None, daily_at: str | None) -> str:
    unit_dir = _systemd_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service = f"""\
[Unit]
Description=Archive RO e-Factura invoices locally
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart={exe} sync
"""
    if every is not None:
        seconds = int(parse_interval(every).total_seconds())
        trigger = f"OnBootSec=2min\nOnUnitActiveSec={seconds}s"
        when = f"every {every}"
    else:
        assert daily_at is not None
        hour, minute = parse_daily_at(daily_at)
        trigger = f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00"
        when = f"daily at {hour:02d}:{minute:02d}"
    timer = f"""\
[Unit]
Description=Timer for anaf-sync

[Timer]
{trigger}
Persistent=true

[Install]
WantedBy=timers.target
"""
    (unit_dir / f"{_UNIT_NAME}.service").write_text(service, encoding="utf-8")
    (unit_dir / f"{_UNIT_NAME}.timer").write_text(timer, encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", f"{_UNIT_NAME}.timer"])
    return (
        f"systemd user timer {_UNIT_NAME!r} installed — runs {when}. "
        "To keep it running while logged out: sudo loginctl enable-linger $USER"
    )


def _systemd_cadence() -> Cadence | None:
    """The cadence written into the timer unit, or ``None`` if it is not ours."""
    try:
        unit = (_systemd_unit_dir() / f"{_UNIT_NAME}.timer").read_text(encoding="utf-8")
    except OSError:
        return None
    if match := _ON_ACTIVE_RE.search(unit):
        return Cadence(every=dt.timedelta(seconds=int(match.group(1))))
    if match := _ON_CALENDAR_RE.search(unit):
        return Cadence(daily_at=(int(match.group(1)), int(match.group(2))))
    return None


# -- macOS (launchd) ---------------------------------------------------------------


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"


def _install_macos(exe: Path, every: str | None, daily_at: str | None) -> str:
    plist: dict[str, object] = {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": [str(exe), "sync"],
        "RunAtLoad": False,
    }
    if every is not None:
        plist["StartInterval"] = int(parse_interval(every).total_seconds())
        when = f"every {every}"
    else:
        assert daily_at is not None
        hour, minute = parse_daily_at(daily_at)
        plist["StartCalendarInterval"] = {"Hour": hour, "Minute": minute}
        when = f"daily at {hour:02d}:{minute:02d}"
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    path.write_bytes(plistlib.dumps(plist))
    _run(["launchctl", "load", str(path)])
    return f"launchd agent {_LAUNCHD_LABEL!r} installed — runs {when}"


def _launchd_cadence() -> Cadence | None:
    """The cadence written into the agent's plist, or ``None`` if it is not ours."""
    try:
        plist = plistlib.loads(_launchd_plist_path().read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return None
    if isinstance(seconds := plist.get("StartInterval"), int) and seconds > 0:
        return Cadence(every=dt.timedelta(seconds=seconds))
    calendar = plist.get("StartCalendarInterval")
    if not isinstance(calendar, dict):
        return None
    hour, minute = calendar.get("Hour"), calendar.get("Minute")
    if not isinstance(hour, int) or not isinstance(minute, int):
        return None
    return Cadence(daily_at=(hour, minute))
