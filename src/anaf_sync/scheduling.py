"""Register anaf-sync with the OS scheduler — no daemon of our own.

Windows uses Task Scheduler (``schtasks``), Linux a systemd *user* timer, and
macOS a launchd agent. Each backend runs ``anaf-sync sync`` with the resolved
console-script path, so the job works regardless of how the venv is activated.
"""

from __future__ import annotations

import datetime as dt
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = ["ScheduleError", "install", "status", "uninstall"]

_TASK_NAME = "AnafSync"  # Windows task / systemd unit / launchd label stem
_LAUNCHD_LABEL = "ro.anaf-sync.sync"

_INTERVAL_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class ScheduleError(RuntimeError):
    """Installing or removing the scheduled job failed."""


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


def _executable() -> Path:
    """The anaf-sync console script, resolved for use outside this shell."""
    found = shutil.which("anaf-sync")
    if found:
        return Path(found).resolve()
    # Fallback: the script sitting next to the current interpreter (venv).
    candidate = Path(sys.executable).with_name(
        "anaf-sync.exe" if sys.platform == "win32" else "anaf-sync"
    )
    if candidate.exists():
        return candidate.resolve()
    raise ScheduleError(
        "cannot locate the `anaf-sync` executable — install the package "
        "(e.g. `uv tool install anaf-sync`) so the script is on PATH"
    )


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ScheduleError(f"{' '.join(cmd[:2])} failed: {detail}")
    return result


def install(*, every: str | None, daily_at: str | None) -> str:
    """Install (or replace) the scheduled job; returns a human summary.

    Exactly one of ``every`` (interval) or ``daily_at`` (HH:MM) must be given.
    """
    if (every is None) == (daily_at is None):
        raise ScheduleError("pass exactly one of --every or --daily-at")
    exe = _executable()
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
    _run(["systemctl", "--user", "disable", "--now", f"{_unit_name()}.timer"])
    for suffix in (".timer", ".service"):
        (_systemd_unit_dir() / f"{_unit_name()}{suffix}").unlink(missing_ok=True)
    _run(["systemctl", "--user", "daemon-reload"])
    return f"removed systemd user timer {_unit_name()!r}"


def status() -> str:
    if sys.platform == "win32":
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", _TASK_NAME, "/V", "/FO", "LIST"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else "not installed"
    if sys.platform == "darwin":
        if not _launchd_plist_path().exists():
            return "not installed"
        result = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL], capture_output=True, text=True
        )
        loaded = "loaded" if result.returncode == 0 else "installed but not loaded"
        return f"launchd agent {_LAUNCHD_LABEL}: {loaded}"
    result = subprocess.run(
        ["systemctl", "--user", "list-timers", f"{_unit_name()}.timer", "--all"],
        capture_output=True,
        text=True,
    )
    out = result.stdout.strip()
    return out if _unit_name() in out else "not installed"


# -- Windows ---------------------------------------------------------------------


def _install_windows(exe: Path, every: str | None, daily_at: str | None) -> str:
    command = f'"{exe}" sync'
    args = ["schtasks", "/Create", "/F", "/TN", _TASK_NAME, "/TR", command]
    if every is not None:
        minutes = int(parse_interval(every).total_seconds() // 60)
        if minutes < 60:
            args += ["/SC", "MINUTE", "/MO", str(minutes)]
        elif minutes % 1440 == 0:
            args += ["/SC", "DAILY", "/MO", str(minutes // 1440)]
        else:
            args += ["/SC", "HOURLY", "/MO", str(max(1, minutes // 60))]
        when = f"every {every}"
    else:
        assert daily_at is not None
        hour, minute = parse_daily_at(daily_at)
        args += ["/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}"]
        when = f"daily at {hour:02d}:{minute:02d}"
    _run(args)
    return f"Task Scheduler task {_TASK_NAME!r} installed — runs {when}"


# -- Linux (systemd user units) ---------------------------------------------------


def _unit_name() -> str:
    return "anaf-sync"


def _systemd_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _install_systemd(exe: Path, every: str | None, daily_at: str | None) -> str:
    unit_dir = _systemd_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    service = f"""\
[Unit]
Description=Archive RO e-Factura invoices locally
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
    (unit_dir / f"{_unit_name()}.service").write_text(service, encoding="utf-8")
    (unit_dir / f"{_unit_name()}.timer").write_text(timer, encoding="utf-8")
    _run(["systemctl", "--user", "daemon-reload"])
    _run(["systemctl", "--user", "enable", "--now", f"{_unit_name()}.timer"])
    return (
        f"systemd user timer {_unit_name()!r} installed — runs {when}. "
        "To keep it running while logged out: sudo loginctl enable-linger $USER"
    )


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
