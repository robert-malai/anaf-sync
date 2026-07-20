"""Romanian value formatting for the Facturi view — pure and unit-tested.

Money uses the Romanian convention (``.`` thousands, ``,`` decimals, e.g.
``4.821,50 RON``); dates are the short day-month form (``18 iul.``). Kept
separate from :mod:`strings` because these are number/date renderings, not
translatable copy — but the abbreviated month names are shared from there.
"""

from __future__ import annotations

import datetime as dt

from .strings import MONTHS_ABBR

__all__ = [
    "EM_DASH",
    "archived_at",
    "money",
    "provenance",
    "short_date",
]

#: The placeholder for an absent value, matching the handoff's em-dash cells.
EM_DASH = "—"


def money(total: float | None, currency: str | None) -> str:
    """``4.821,50 RON`` — Romanian grouping, currency suffix; ``—`` if absent."""
    if total is None:
        return EM_DASH
    # Format with English separators, then swap into the Romanian convention.
    grouped = f"{total:,.2f}"  # e.g. "4,821.50"
    grouped = grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{grouped} {currency}" if currency else grouped


def short_date(value: dt.date | None) -> str:
    """``18 iul.`` — day + abbreviated Romanian month; ``—`` if absent."""
    if value is None:
        return EM_DASH
    return f"{value.day} {MONTHS_ABBR[value.month - 1]}"


def archived_at(value: dt.datetime | None) -> str:
    """``18 iul. 2026, 14:32`` — tabular timestamp for the provenance block."""
    if value is None:
        return EM_DASH
    local = value.astimezone()
    return f"{local.day} {MONTHS_ABBR[local.month - 1]} {local.year}, {local:%H:%M}"


def provenance(value: str | None) -> str:
    """A raw provenance value (message id, type…) or the em-dash placeholder."""
    return value if value else EM_DASH
