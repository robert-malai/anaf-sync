"""Interval/time parsing and schedule mapping (no OS calls here)."""

import datetime as dt

import pytest

from anaf_sync.scheduling import (
    ScheduleError,
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
