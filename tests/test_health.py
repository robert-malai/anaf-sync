"""Pure health derivation: purge countdown, delay days, three-state folding."""

import datetime as dt

from anaf_sync.health import (
    DELAY_THRESHOLD_DAYS,
    days_until_purge,
    derive_health,
    upload_delay_days,
)
from anaf_sync.state import FailureRecord, RunRecord

_NOW = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.UTC)


def _failure(
    first: dt.datetime, *, attempts: int = 1, error: str = "boom"
) -> FailureRecord:
    return FailureRecord(
        first_failed_at=first,
        last_failed_at=first,
        attempts=attempts,
        error=error,
    )


def _ok_run(when: dt.datetime = _NOW) -> RunRecord:
    return RunRecord(finished_at=when, outcome="ok", listed=3, archived=3)


# -- days_until_purge ---------------------------------------------------------


def test_days_until_purge_from_first_failure() -> None:
    # First failed 51 days ago → 60 - 51 = 9 days left.
    first = _NOW - dt.timedelta(days=51)
    assert days_until_purge(_failure(first), _NOW) == 9


def test_days_until_purge_prefers_created_when_given() -> None:
    first = _NOW - dt.timedelta(days=51)  # would give 9
    created = _NOW - dt.timedelta(days=58)  # tighter: 60 - 58 = 2
    assert days_until_purge(_failure(first), _NOW, created=created) == 2


def test_days_until_purge_goes_negative_past_the_window() -> None:
    first = _NOW - dt.timedelta(days=70)
    assert days_until_purge(_failure(first), _NOW) == -10


def test_days_until_purge_tolerates_naive_created() -> None:
    naive = dt.datetime(2026, 7, 1, 9, 0)  # no tzinfo — treated as UTC
    # purge = 2026-08-30 09:00; now = 2026-07-20 12:00 → 40 whole days.
    assert days_until_purge(_failure(_NOW), _NOW, created=naive) == 40


# -- upload_delay_days --------------------------------------------------------


def test_upload_delay_days_none_when_either_missing() -> None:
    assert upload_delay_days(None, _NOW) is None
    assert upload_delay_days(dt.date(2026, 7, 1), None) is None


def test_upload_delay_days_counts_whole_days() -> None:
    # FF-88214 from the handoff: issued 11 iul., uploaded 19 iul. → 8 days.
    delay = upload_delay_days(dt.date(2026, 7, 11), dt.datetime(2026, 7, 19, 8, 30))
    assert delay == 8


def test_delay_threshold_boundary() -> None:
    # Exactly 5 days is NOT delayed; 6 is (delayed = delay > threshold).
    five = upload_delay_days(dt.date(2026, 7, 1), dt.datetime(2026, 7, 6, 0, 0))
    six = upload_delay_days(dt.date(2026, 7, 1), dt.datetime(2026, 7, 7, 0, 0))
    assert five == DELAY_THRESHOLD_DAYS
    assert five is not None and not (five > DELAY_THRESHOLD_DAYS)
    assert six is not None and six > DELAY_THRESHOLD_DAYS


# -- derive_health: state rules ----------------------------------------------


def test_ok_when_last_run_ok_and_no_failures() -> None:
    health = derive_health(_ok_run(), {}, _NOW)
    assert health.state == "ok"
    assert health.failure_count == 0
    assert health.worst_failure is None
    assert health.auth_broken is False


def test_warn_on_any_failure() -> None:
    failures = {"m1": _failure(_NOW - dt.timedelta(days=10))}
    health = derive_health(_ok_run(), failures, _NOW)
    assert health.state == "warn"
    assert health.failure_count == 1


def test_err_when_last_run_crashed() -> None:
    crashed = RunRecord(finished_at=_NOW, outcome="crashed", error_kind="RuntimeError")
    assert derive_health(crashed, {}, _NOW).state == "err"


def test_err_and_auth_broken_on_auth_error() -> None:
    failed = RunRecord(
        finished_at=_NOW, outcome="failed", error="expired", error_kind="AnafAuthError"
    )
    health = derive_health(failed, {}, _NOW)
    assert health.state == "err"
    assert health.auth_broken is True


def test_err_on_config_error() -> None:
    failed = RunRecord(finished_at=_NOW, outcome="failed", error_kind="AnafConfigError")
    assert derive_health(failed, {}, _NOW).state == "err"


def test_failed_run_without_auth_kind_is_not_err_by_itself() -> None:
    # Per-message download failures: outcome "failed", no error_kind.
    failed = RunRecord(finished_at=_NOW, outcome="failed", failures=1)
    assert derive_health(failed, {}, _NOW).state == "ok"
    assert derive_health(failed, {}, _NOW).auth_broken is False


def test_err_when_schedule_went_stale() -> None:
    interval = dt.timedelta(hours=6)
    stale_run = _ok_run(_NOW - dt.timedelta(hours=13))  # > 2× interval
    assert derive_health(stale_run, {}, _NOW, interval=interval).state == "err"


def test_fresh_run_within_interval_is_not_stale() -> None:
    interval = dt.timedelta(hours=6)
    recent = _ok_run(_NOW - dt.timedelta(hours=5))
    assert derive_health(recent, {}, _NOW, interval=interval).state == "ok"


def test_no_last_run_is_not_stale() -> None:
    # A fresh install (never synced) shows ok, not err.
    assert derive_health(None, {}, _NOW, interval=dt.timedelta(hours=6)).state == "ok"


def test_err_wins_over_warn() -> None:
    crashed = RunRecord(finished_at=_NOW, outcome="crashed")
    failures = {"m1": _failure(_NOW)}
    health = derive_health(crashed, failures, _NOW)
    assert health.state == "err"  # not "warn", despite the failure present
    assert health.failure_count == 1


# -- derive_health: worst-failure selection ----------------------------------


def test_worst_failure_is_the_one_closest_to_purging() -> None:
    failures = {
        "young": _failure(_NOW - dt.timedelta(days=5)),  # 55 days left
        "old": _failure(_NOW - dt.timedelta(days=51), attempts=6),  # 9 days left
    }
    health = derive_health(
        _ok_run(),
        failures,
        _NOW,
        partners={"old": "TERMOENERGIA S.R.L."},
    )
    assert health.worst_failure is not None
    assert health.worst_failure.message_id == "old"
    assert health.worst_failure.partner_name == "TERMOENERGIA S.R.L."
    assert health.worst_failure.days_left == 9
    assert health.worst_failure.attempts == 6


def test_worst_failure_partner_none_when_unknown() -> None:
    failures = {"m1": _failure(_NOW - dt.timedelta(days=1))}
    health = derive_health(_ok_run(), failures, _NOW)
    assert health.worst_failure is not None
    assert health.worst_failure.partner_name is None
