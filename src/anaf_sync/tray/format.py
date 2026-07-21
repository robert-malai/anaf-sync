"""Pure Romanian rendering for the tray — values, pluralisation, phrases.

Money uses the Romanian convention (``.`` thousands, ``,`` decimals, e.g.
``4.821,50 RON``); dates are the Romanian numeric form ``zz.ll.aaaa``
(``18.07.2026`` — DESIGN.md §10 for why, and for why ISO never leaks into
the UI). The
language logic that several views share lives here too — :func:`noun`
(Romanian pluralisation), :func:`relative_time`, the direction pill labels,
the SPV-expiry phrase — pure and unit-tested. All remaining copy is inlined
where it is shown: the UI is Romanian-only by construction (RO e-Factura only
serves Romanian fiscal entities), so there is no translation layer.
"""

from __future__ import annotations

import datetime as dt

__all__ = [
    "EM_DASH",
    "archived_at",
    "direction_label",
    "money",
    "noun",
    "provenance",
    "relative_time",
    "short_date",
    "spv_expiry",
]

#: The placeholder for an absent value, matching the handoff's em-dash cells.
EM_DASH = "—"

#: Direction pill labels — shared by the table delegate and the details pane.
_DIRECTION_LABELS = {
    "received": "primită",
    "sent": "trimisă",
    "failing": "eșuată",
}


def money(total: float | None, currency: str | None) -> str:
    """``4.821,50 RON`` — Romanian grouping, currency suffix; ``—`` if absent."""
    if total is None:
        return EM_DASH
    # Format with English separators, then swap into the Romanian convention.
    grouped = f"{total:,.2f}"  # e.g. "4,821.50"
    grouped = grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{grouped} {currency}" if currency else grouped


def short_date(value: dt.date | None) -> str:
    """``18.07.2026`` — the Romanian ``zz.ll.aaaa`` form; ``—`` if absent."""
    if value is None:
        return EM_DASH
    return f"{value:%d.%m.%Y}"


def archived_at(value: dt.datetime | None) -> str:
    """``18.07.2026, 14:32`` — tabular timestamp for the provenance block."""
    if value is None:
        return EM_DASH
    return f"{value.astimezone():%d.%m.%Y, %H:%M}"


def provenance(value: str | None) -> str:
    """A raw provenance value (message id, type…) or the em-dash placeholder."""
    return value if value else EM_DASH


def direction_label(direction: str) -> str:
    """``primită`` / ``trimisă`` / ``eșuată`` — empty for unknown directions."""
    return _DIRECTION_LABELS.get(direction, "")


def _needs_de(n: int) -> bool:
    """Romanian inserts ``de`` before the noun for 0 and 20+ (per the 100-rule)."""
    remainder = n % 100
    return n >= 20 and (remainder == 0 or remainder >= 20)


def noun(n: int, singular: str, plural: str) -> str:
    """``"1 factură"`` / ``"3 facturi"`` / ``"21 de facturi"``."""
    if n == 1:
        return f"{n} {singular}"
    if _needs_de(n):
        return f"{n} de {plural}"
    return f"{n} {plural}"


def relative_time(when: dt.datetime, now: dt.datetime) -> str:
    """A human, Romanian relative time: ``"acum 2 ore"``, ``"ieri, 14:32"``.

    ``when`` and ``now`` must share awareness (both naive or both aware); the
    caller converts UTC timestamps to local time first, so ``%H:%M`` reads as
    the wall clock the user knows.
    """
    delta = now - when
    seconds = delta.total_seconds()
    if seconds < 60:
        return "chiar acum"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"acum {noun(minutes, 'minut', 'minute')}"
    day_gap = (now.date() - when.date()).days
    if day_gap == 0:
        hours = int(seconds // 3600)
        return f"acum {noun(hours, 'oră', 'ore')}"
    if day_gap == 1:
        return f"ieri, {when:%H:%M}"
    if day_gap <= 6:
        return f"acum {noun(day_gap, 'zi', 'zile')}"
    return short_date(when.date())


def spv_expiry(days_left: int) -> str:
    """``"expiră din SPV în 9 zile"`` / ``"a expirat din SPV"`` (lowercase)."""
    if days_left <= 0:
        return "a expirat din SPV"
    return f"expiră din SPV în {noun(days_left, 'zi', 'zile')}"
