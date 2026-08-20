"""The named date spans behind the two date filters — pure, no Qt needed."""

from __future__ import annotations

import datetime as dt

import pytest

from anaf_sync.tray.period import (
    ALL,
    CUSTOM,
    LAST_3_MONTHS,
    THIS_MONTH,
    DateSpan,
    month_end,
)


def test_all_is_the_unfiltered_span() -> None:
    span = DateSpan()
    assert not span.active
    assert span.resolve(dt.date(2026, 7, 20)) == (None, None)


def test_this_month_spans_the_whole_month() -> None:
    span = DateSpan(mode=THIS_MONTH)
    assert span.resolve(dt.date(2026, 7, 20)) == (
        dt.date(2026, 7, 1),
        dt.date(2026, 7, 31),
    )


def test_this_month_is_recomputed_not_frozen() -> None:
    """The mode is stored, not the range, so it follows the calendar over."""
    span = DateSpan(mode=THIS_MONTH)
    assert span.resolve(dt.date(2026, 8, 1))[0] == dt.date(2026, 8, 1)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (dt.date(2026, 7, 20), dt.date(2026, 4, 20)),
        # Across the year boundary.
        (dt.date(2026, 2, 10), dt.date(2025, 11, 10)),
        # 31 May less three months is the end of February, not an invalid date.
        (dt.date(2026, 5, 31), dt.date(2026, 2, 28)),
    ],
)
def test_last_three_months_clamps_into_shorter_months(
    today: dt.date, expected: dt.date
) -> None:
    assert DateSpan(mode=LAST_3_MONTHS).resolve(today) == (expected, today)


def test_custom_keeps_its_own_dates() -> None:
    span = DateSpan(CUSTOM, dt.date(2026, 7, 8), dt.date(2026, 7, 18))
    assert span.resolve(dt.date(2026, 7, 20)) == (
        dt.date(2026, 7, 8),
        dt.date(2026, 7, 18),
    )


def test_half_filled_custom_span_stays_open_on_that_side() -> None:
    """A form mid-edit should narrow the catalog, never blank it."""
    span = DateSpan(CUSTOM, dt.date(2026, 7, 8), None)
    assert span.resolve(dt.date(2026, 7, 20)) == (dt.date(2026, 7, 8), None)


def test_labels_name_the_preset_but_recite_a_custom_range() -> None:
    today = dt.date(2026, 7, 20)
    assert DateSpan(mode=THIS_MONTH).label(today) == THIS_MONTH
    assert DateSpan(mode=ALL).label(today) == ALL
    span = DateSpan(CUSTOM, dt.date(2026, 7, 8), dt.date(2026, 7, 18))
    assert span.label(today) == "08.07.2026 – 18.07.2026"
    assert DateSpan(CUSTOM, dt.date(2026, 7, 8), None).label(today) == (
        "de la 08.07.2026"
    )
    assert DateSpan(CUSTOM, None, dt.date(2026, 7, 18)).label(today) == (
        "până la 18.07.2026"
    )


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (dt.date(2026, 2, 3), dt.date(2026, 2, 28)),
        (dt.date(2024, 2, 3), dt.date(2024, 2, 29)),  # leap year
        (dt.date(2026, 12, 9), dt.date(2026, 12, 31)),
    ],
)
def test_month_end(day: dt.date, expected: dt.date) -> None:
    assert month_end(day) == expected
