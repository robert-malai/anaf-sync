"""Interval/time parsing for the scheduler (no OS calls here)."""

import datetime as dt

import pytest

from anaf_sync.scheduling import ScheduleError, parse_daily_at, parse_interval


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
