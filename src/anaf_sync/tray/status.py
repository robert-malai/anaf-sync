"""Bridge the core archive state to a tray-ready display model — pure, no Qt.

:func:`load_status` reads ``state.db`` (read-only) and ``config.toml``, folds
them through :func:`anaf_sync.health.derive_health`, and returns a
:class:`TrayStatus` the menu can render directly. A broken or missing config is
tolerated as an error state rather than an exception, so the tray always paints
*something*.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

from ..config import load_config
from ..health import Health, HealthState, derive_health
from ..state import Archive, FailureRecord, RunRecord
from . import strings

__all__ = ["TrayStatus", "load_status"]

#: Shown in the red alert row when ``config.toml`` cannot be parsed.
_CONFIG_INVALID = "Configurație invalidă — verificați config.toml"


@dataclasses.dataclass(frozen=True)
class TrayStatus:
    """Everything the tray icon + menu need for one refresh."""

    state: HealthState
    headline: str
    subline: str
    #: Alert-row body (``None`` in the ok state). For the auth error the
    #: trailing mono command lives in ``alert_command`` so the UI can chip it.
    alert_text: str | None
    alert_command: str | None
    #: Colour family for the alert row (amber for warn, red for err).
    alert_state: HealthState
    archived_count: int
    #: Resolved archive directory for "Deschide dosarul arhivei"; ``None`` when
    #: the config is unreadable.
    output_dir: Path | None


def load_status(
    *,
    state_path: Path,
    config_path: Path,
    now: dt.datetime,
    interval: dt.timedelta | None = None,
) -> TrayStatus:
    """Assemble the tray display model from the archive and config on disk."""
    output_dir, config_error = _read_config(config_path)
    count, failures, last_run = _read_archive(state_path)

    health = derive_health(last_run, failures, now, interval=interval)
    state: HealthState = "err" if config_error else health.state

    alert_text, alert_command, alert_state = _alert(state, health, config_error)
    return TrayStatus(
        state=state,
        headline=_headline(state),
        subline=_subline(state, last_run, now),
        alert_text=alert_text,
        alert_command=alert_command,
        alert_state=alert_state,
        archived_count=count,
        output_dir=output_dir,
    )


def _read_config(config_path: Path) -> tuple[Path | None, str | None]:
    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        return None, str(exc)
    return cfg.output.resolved_directory, None


def _read_archive(
    state_path: Path,
) -> tuple[int, dict[str, FailureRecord], RunRecord | None]:
    if not state_path.exists():
        return 0, {}, None
    with Archive.open_readonly(state_path) as archive:
        return archive.count, archive.failures, archive.last_run()


def _headline(state: HealthState) -> str:
    if state == "ok":
        return strings.ARCHIVE_UP_TO_DATE
    if state == "warn":
        return strings.NEEDS_ATTENTION
    return strings.SYNC_BROKEN


def _subline(state: HealthState, last_run: RunRecord | None, now: dt.datetime) -> str:
    if last_run is None:
        return strings.never_synced_subline()
    relative = strings.relative_time(
        last_run.finished_at.astimezone(), now.astimezone()
    )
    if state == "err":
        return strings.last_success_subline(relative)
    new_count = last_run.archived if state == "ok" else 0
    return strings.last_sync_subline(relative, new_count)


def _alert(
    state: HealthState,
    health: Health,
    config_error: str | None,
) -> tuple[str | None, str | None, HealthState]:
    if config_error is not None:
        return _CONFIG_INVALID, None, "err"
    if state == "err":
        if health.auth_broken:
            return strings.AUTH_EXPIRED_PREFIX, strings.AUTH_LOGIN_COMMAND, "err"
        return strings.generic_error_alert(), None, "err"
    if state == "warn":
        worst = health.worst_failure
        partner = worst.partner_name if worst else None
        days_left = worst.days_left if worst else 0
        return (
            strings.failing_alert(health.failure_count, partner, days_left),
            None,
            "warn",
        )
    return None, None, "ok"
