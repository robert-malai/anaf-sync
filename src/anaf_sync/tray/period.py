"""Named date spans behind the Emisă and Încărcată filters — pure, no Qt.

A span is a *mode* plus, when that mode is "Personalizat…", two explicit dates.
Storing the mode rather than the resolved range is what lets "Luna curentă"
still mean the current month after midnight on the first, and what lets the
active-filter bar name a filter ("Luna curentă") instead of reciting its
endpoints. :meth:`DateSpan.resolve` turns one into the bounds SQL wants.

Both date columns use the same vocabulary, so this module knows nothing about
which one it is filtering.
"""

from __future__ import annotations

import calendar
import dataclasses
import datetime as dt

from .format import short_date

__all__ = ["ALL", "CUSTOM", "LAST_3_MONTHS", "MODES", "THIS_MONTH", "DateSpan"]

ALL = "Toate"
THIS_MONTH = "Luna curentă"
LAST_3_MONTHS = "Ultimele 3 luni"
CUSTOM = "Personalizat…"

#: The four choices a date popover offers, in the order it shows them.
MODES = (ALL, THIS_MONTH, LAST_3_MONTHS, CUSTOM)

_MONTHS_BACK = 3


def month_end(day: dt.date) -> dt.date:
    """The last day of ``day``'s month."""
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _months_before(day: dt.date, months: int) -> dt.date:
    """``day`` shifted back whole months, clamped into a shorter month.

    31 May less three months is 28 (or 29) February, not an invalid date.
    """
    month = day.month - months
    year = day.year
    while month <= 0:
        month += 12
        year -= 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclasses.dataclass(frozen=True)
class DateSpan:
    """One date column's filter: a named preset, or an explicit range."""

    mode: str = ALL
    start: dt.date | None = None
    end: dt.date | None = None

    @property
    def active(self) -> bool:
        """Whether this span filters anything at all."""
        return self.mode != ALL

    def resolve(self, today: dt.date) -> tuple[dt.date | None, dt.date | None]:
        """The inclusive ``(from, to)`` bounds, or ``(None, None)`` for no filter.

        A custom span with a missing end is left open on that side rather than
        treated as empty: a half-filled form should narrow the list, not blank it.
        """
        if self.mode == THIS_MONTH:
            return today.replace(day=1), month_end(today)
        if self.mode == LAST_3_MONTHS:
            return _months_before(today, _MONTHS_BACK), today
        if self.mode == CUSTOM:
            return self.start, self.end
        return None, None

    def label(self, today: dt.date) -> str:
        """How the active-filter bar names this span."""
        if self.mode != CUSTOM:
            return self.mode
        start, end = self.resolve(today)
        if start is None and end is None:
            return CUSTOM
        if start is None:
            return f"până la {short_date(end)}"
        if end is None:
            return f"de la {short_date(start)}"
        return f"{short_date(start)} – {short_date(end)}"
