"""Pure health derivation: purge countdown, delay days, three-state folding."""

import datetime as dt

from anaf_sync.health import (
    days_until_purge,
    derive_health,
    is_delayed,
    upload_delay_working_days,
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


def test_days_until_purge_goes_negative_past_the_window() -> None:
    first = _NOW - dt.timedelta(days=70)
    assert days_until_purge(_failure(first), _NOW) == -10


def test_days_until_purge_tolerates_naive_timestamps() -> None:
    naive = dt.datetime(2026, 7, 1, 9, 0)  # no tzinfo — treated as UTC
    # purge = 2026-08-30 09:00; now = 2026-07-20 12:00 → 40 whole days.
    assert days_until_purge(_failure(naive), _NOW) == 40


# -- upload_delay_working_days / is_delayed -----------------------------------


def test_upload_delay_none_when_either_missing() -> None:
    assert upload_delay_working_days(None, _NOW) is None
    assert upload_delay_working_days(dt.date(2026, 7, 1), None) is None


def test_upload_delay_counts_working_days_only() -> None:
    # FF-88214 from the handoff: issued Saturday 11 iul., uploaded Monday
    # 20 iul. → 6 working days (13–17 iul. plus the 20th; both weekends skip).
    delay = upload_delay_working_days(
        dt.date(2026, 7, 11), dt.datetime(2026, 7, 20, 8, 30)
    )
    assert delay == 6


def test_upload_delay_over_one_weekend_is_one_day() -> None:
    # Issued Friday, uploaded Monday: the calendar says 3, the clock says 1.
    delay = upload_delay_working_days(
        dt.date(2026, 7, 3), dt.datetime(2026, 7, 6, 9, 0)
    )
    assert delay == 1


def test_upload_delay_weekend_upload_counts_no_weekend_days() -> None:
    # Issued Monday 13 iul., uploaded Saturday 18 iul. → Tue–Fri = 4.
    delay = upload_delay_working_days(
        dt.date(2026, 7, 13), dt.datetime(2026, 7, 18, 9, 0)
    )
    assert delay == 4


def test_delay_threshold_boundary_in_working_days() -> None:
    # Issued Monday 6 iul.: the 5th working day is Monday 13 iul. — on time;
    # Tuesday 14 iul. is the 6th and delayed (delayed = delay > threshold).
    issue = dt.date(2026, 7, 6)
    assert is_delayed(issue, dt.datetime(2026, 7, 13, 23, 0)) is False
    assert is_delayed(issue, dt.datetime(2026, 7, 14, 0, 0)) is True


def test_is_delayed_false_when_either_date_missing() -> None:
    assert is_delayed(None, _NOW) is False
    assert is_delayed(dt.date(2026, 7, 1), None) is False


# -- derive_health: state rules ----------------------------------------------


def test_ok_when_last_run_ok_and_no_failures() -> None:
    health = derive_health(_ok_run(), {}, _NOW)
    assert health.state == "ok"
    assert health.failure_count == 0
    assert health.worst_days_left is None
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


def test_err_wins_over_warn() -> None:
    crashed = RunRecord(finished_at=_NOW, outcome="crashed")
    failures = {"m1": _failure(_NOW)}
    health = derive_health(crashed, failures, _NOW)
    assert health.state == "err"  # not "warn", despite the failure present
    assert health.failure_count == 1


# -- derive_health: worst-failure countdown -----------------------------------


def test_worst_days_left_is_the_smallest_countdown() -> None:
    failures = {
        "young": _failure(_NOW - dt.timedelta(days=5)),  # 55 days left
        "old": _failure(_NOW - dt.timedelta(days=51)),  # 9 days left
    }
    health = derive_health(_ok_run(), failures, _NOW)
    assert health.worst_days_left == 9
