"""Pure health derivation shared by ``anaf-sync status`` and the desktop tray.

These functions turn the raw archive state — the last run outcome and the
failure traces — into the three-state summary the UI paints (ok / warn / err)
and the purge countdown ``status`` prints. No Qt, no IO: everything here is a
value transformation over :mod:`anaf_sync.state` records, unit-tested in
isolation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Mapping
from typing import Literal

from .state import FailureRecord, RunRecord

__all__ = [
    "DELAY_THRESHOLD_DAYS",
    "Health",
    "WorstFailure",
    "days_until_purge",
    "derive_health",
    "upload_delay_days",
]

#: How long ANAF retains an e-Factura message before purging it from SPV.
PURGE_WINDOW_DAYS = 60

#: An invoice uploaded to SPV more than this many days after its issue date is
#: flagged as *declarată cu întârziere* (delayed). A single constant for now;
#: promoting it to a config key is parked until asked (see the plan's open
#: questions).
DELAY_THRESHOLD_DAYS = 5

#: Last-run ``error_kind`` values that mean the sync itself is broken (bad
#: credentials or configuration), not merely that some downloads failed.
AUTH_CONFIG_ERROR_KINDS = frozenset({"AnafAuthError", "AnafConfigError"})

HealthState = Literal["ok", "warn", "err"]


def _as_utc(value: dt.datetime) -> dt.datetime:
    """Attach UTC to a naive datetime so arithmetic never mixes aware/naive."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def days_until_purge(
    failure: FailureRecord,
    now: dt.datetime,
    *,
    created: dt.datetime | None = None,
) -> int:
    """Whole days until ANAF's 60-day window closes on a failing message.

    The window is measured from when the message entered SPV. That exact time
    is only known when the message was catalogued (``created``); a message that
    only ever failed was never catalogued, so we approximate with
    ``first_failed_at`` — the message was already in SPV by the first failure,
    making this a conservative (never-too-late) estimate. A negative result
    means the window has, by this estimate, already closed.
    """
    reference = _as_utc(created if created is not None else failure.first_failed_at)
    purge = reference + dt.timedelta(days=PURGE_WINDOW_DAYS)
    return (purge - _as_utc(now)).days


def upload_delay_days(issue: dt.date | None, created: dt.datetime | None) -> int | None:
    """Whole days between issue date and SPV upload; ``None`` if either is missing.

    Compare against :data:`DELAY_THRESHOLD_DAYS` to decide whether a row is
    *delayed* — the comparison (``> threshold``) lives with the caller so the
    raw count stays reusable.
    """
    if issue is None or created is None:
        return None
    return (created.date() - issue).days


@dataclasses.dataclass(frozen=True)
class WorstFailure:
    """The most urgent failing message — the one closest to ageing out."""

    message_id: str
    partner_name: str | None
    days_left: int
    attempts: int
    last_error: str


@dataclasses.dataclass(frozen=True)
class Health:
    """The derived health of the archive, everything the tray menu needs."""

    state: HealthState
    failure_count: int
    worst_failure: WorstFailure | None
    #: The last sync failed because auth/config is broken (drives the red
    #: "rulați ``anafpy auth login``" alert).
    auth_broken: bool
    last_run: RunRecord | None


def derive_health(
    last_run: RunRecord | None,
    failures: Mapping[str, FailureRecord],
    now: dt.datetime,
    *,
    interval: dt.timedelta | None = None,
    partners: Mapping[str, str | None] | None = None,
    created: Mapping[str, dt.datetime | None] | None = None,
) -> Health:
    """Fold the last run and failure traces into a three-state summary.

    Rules (``err`` wins over ``warn``):

    - **err** — the last run crashed, or failed with an auth/config
      ``error_kind``, or (when ``interval`` is known) no run has completed
      within twice the scheduled interval (the schedule has silently stopped).
    - **warn** — any failure trace is present.
    - **ok** — otherwise.

    ``partners`` / ``created`` optionally enrich the worst-failure line by
    message id (partner name for the alert; SPV-entry time for a tighter purge
    estimate); both default to empty.
    """
    partners = partners or {}
    created = created or {}

    worst = _worst_failure(failures, now, partners, created)
    auth_broken = bool(
        last_run is not None
        and last_run.outcome == "failed"
        and last_run.error_kind in AUTH_CONFIG_ERROR_KINDS
    )
    crashed = last_run is not None and last_run.outcome == "crashed"
    stale = _is_stale(last_run, now, interval)

    state: HealthState
    if crashed or auth_broken or stale:
        state = "err"
    elif failures:
        state = "warn"
    else:
        state = "ok"

    return Health(
        state=state,
        failure_count=len(failures),
        worst_failure=worst,
        auth_broken=auth_broken,
        last_run=last_run,
    )


def _worst_failure(
    failures: Mapping[str, FailureRecord],
    now: dt.datetime,
    partners: Mapping[str, str | None],
    created: Mapping[str, dt.datetime | None],
) -> WorstFailure | None:
    if not failures:
        return None
    message_id, record = min(
        failures.items(),
        key=lambda kv: days_until_purge(kv[1], now, created=created.get(kv[0])),
    )
    return WorstFailure(
        message_id=message_id,
        partner_name=partners.get(message_id),
        days_left=days_until_purge(record, now, created=created.get(message_id)),
        attempts=record.attempts,
        last_error=record.error,
    )


def _is_stale(
    last_run: RunRecord | None,
    now: dt.datetime,
    interval: dt.timedelta | None,
) -> bool:
    """The schedule has silently stopped: no run within twice its interval.

    Only meaningful once a schedule interval is known and at least one run has
    been recorded — a fresh install with no runs yet is not "stale".
    """
    if interval is None or last_run is None:
        return False
    return _as_utc(now) - _as_utc(last_run.finished_at) > 2 * interval
