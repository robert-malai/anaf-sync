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
from . import format as fmt

__all__ = ["TrayStatus", "load_status"]

# -- Headlines (design mock §1a) -----------------------------------------------

ARCHIVE_UP_TO_DATE = "Arhiva este la zi"
NEEDS_ATTENTION = "Necesită atenție"
SYNC_BROKEN = "Sincronizarea nu funcționează"

NEVER_SYNCED = "Nu s-a sincronizat încă"

#: Rendered before the mono ``anafpy auth login`` chip in the red alert row.
AUTH_EXPIRED_PREFIX = "Autentificarea ANAF a expirat — rulați "
AUTH_LOGIN_COMMAND = "anafpy auth login"

#: Red row when the last run broke for a non-auth reason (a crash).
_GENERIC_ERROR = "Ultima sincronizare a eșuat — verificați jurnalul aplicației"

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
) -> TrayStatus:
    """Assemble the tray display model from the archive and config on disk."""
    output_dir, config_error = _read_config(config_path)
    count, failures, last_run = _read_archive(state_path)

    health = derive_health(last_run, failures, now)
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
        return ARCHIVE_UP_TO_DATE
    if state == "warn":
        return NEEDS_ATTENTION
    return SYNC_BROKEN


def _new_invoices_phrase(count: int) -> str:
    """``"3 facturi noi"`` / ``"1 factură nouă"`` (agreeing adjective)."""
    adjective = "nouă" if count == 1 else "noi"
    return f"{fmt.noun(count, 'factură', 'facturi')} {adjective}"


def _subline(state: HealthState, last_run: RunRecord | None, now: dt.datetime) -> str:
    if last_run is None:
        return NEVER_SYNCED
    relative = fmt.relative_time(last_run.finished_at.astimezone(), now.astimezone())
    if state == "err":
        return f"Ultima sincronizare reușită: {relative}"
    base = f"Ultima sincronizare: {relative}"
    new_count = last_run.archived if state == "ok" else 0
    if new_count > 0:
        return f"{base} · {_new_invoices_phrase(new_count)}"
    return base


def _failing_alert(count: int, days_left: int) -> str:
    """Amber row: ``"1 factură eșuează repetat — expiră din SPV în …"``."""
    failing = f"{fmt.noun(count, 'factură', 'facturi')} eșuează repetat"
    return f"{failing} — {fmt.spv_expiry(days_left)}"


def _alert(
    state: HealthState,
    health: Health,
    config_error: str | None,
) -> tuple[str | None, str | None, HealthState]:
    if config_error is not None:
        return _CONFIG_INVALID, None, "err"
    if state == "err":
        if health.auth_broken:
            return AUTH_EXPIRED_PREFIX, AUTH_LOGIN_COMMAND, "err"
        return _GENERIC_ERROR, None, "err"
    if state == "warn":
        days_left = health.worst_days_left if health.worst_days_left is not None else 0
        return _failing_alert(health.failure_count, days_left), None, "warn"
    return None, None, "ok"
