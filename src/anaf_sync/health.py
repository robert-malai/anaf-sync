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
    "days_until_purge",
    "derive_health",
    "is_delayed",
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


def days_until_purge(failure: FailureRecord, now: dt.datetime) -> int:
    """Whole days until ANAF's 60-day window closes on a failing message.

    The window is measured from when the message entered SPV. A message that
    only ever failed was never catalogued, so we approximate with
    ``first_failed_at`` — the message was already in SPV by the first failure,
    making this a conservative (never-too-late) estimate. A negative result
    means the window has, by this estimate, already closed.
    """
    purge = _as_utc(failure.first_failed_at) + dt.timedelta(days=PURGE_WINDOW_DAYS)
    return (purge - _as_utc(now)).days


def upload_delay_days(issue: dt.date | None, created: dt.datetime | None) -> int | None:
    """Whole days between issue date and SPV upload; ``None`` if either is missing.

    Compare against :data:`DELAY_THRESHOLD_DAYS` to decide whether a row is
    *delayed* — or use :func:`is_delayed`, which does exactly that.
    """
    if issue is None or created is None:
        return None
    return (created.date() - issue).days


def is_delayed(issue: dt.date | None, created: dt.datetime | None) -> bool:
    """Whether an invoice was uploaded past the delay threshold.

    ``False`` when either date is unknown — a row can only be flagged
    *declarată cu întârziere* on evidence.
    """
    delay = upload_delay_days(issue, created)
    return delay is not None and delay > DELAY_THRESHOLD_DAYS


@dataclasses.dataclass(frozen=True)
class Health:
    """The derived health of the archive, everything the tray menu needs."""

    state: HealthState
    failure_count: int
    #: Purge countdown of the most urgent failing message (the one closest to
    #: ageing out of SPV); ``None`` when nothing is failing.
    worst_days_left: int | None
    #: The last sync failed because auth/config is broken (drives the red
    #: "rulați ``anafpy auth login``" alert).
    auth_broken: bool


def derive_health(
    last_run: RunRecord | None,
    failures: Mapping[str, FailureRecord],
    now: dt.datetime,
) -> Health:
    """Fold the last run and failure traces into a three-state summary.

    Rules (``err`` wins over ``warn``):

    - **err** — the last run crashed, or failed with an auth/config
      ``error_kind``.
    - **warn** — any failure trace is present.
    - **ok** — otherwise.
    """
    auth_broken = bool(
        last_run is not None
        and last_run.outcome == "failed"
        and last_run.error_kind in AUTH_CONFIG_ERROR_KINDS
    )
    crashed = last_run is not None and last_run.outcome == "crashed"

    state: HealthState
    if crashed or auth_broken:
        state = "err"
    elif failures:
        state = "warn"
    else:
        state = "ok"

    return Health(
        state=state,
        failure_count=len(failures),
        worst_days_left=(
            min(days_until_purge(record, now) for record in failures.values())
            if failures
            else None
        ),
        auth_broken=auth_broken,
    )
