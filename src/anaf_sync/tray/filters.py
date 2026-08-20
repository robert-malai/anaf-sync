"""Everything the header's filters add up to — pure, no Qt.

:class:`FilterState` is the one place the Facturi window's filter state lives.
Three readers derive from it and none of them keeps a copy: the model gets a
:class:`~anaf_sync.tray.models.CatalogFilters` to query with, the header gets
the set of columns whose funnel should read as active, and the active-filter
bar gets one removable chip per filter. Keeping the derivations here rather
than in the window is what makes them testable without a display, and what
stops the three views from disagreeing about whether a filter is on.

Search is deliberately not a chip: it has its own visible field in the toolbar,
so echoing it in the bar would say the same thing twice.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

from ..state import CatalogQuery
from .format import direction_label
from .models import (
    ALL_DIRECTIONS,
    COL_DIRECTION,
    COL_FROM_CIF,
    COL_ISSUED,
    COL_NUMBER,
    COL_PARTNER,
    COL_TO_CIF,
    COL_UPLOADED,
    CatalogFilters,
    split_direction_choice,
)
from .period import DateSpan

__all__ = [
    "KEY_DELAYED",
    "KEY_DIRECTIONS",
    "KEY_FROM_CIF",
    "KEY_ISSUED",
    "KEY_NUMBER",
    "KEY_PARTNER",
    "KEY_TO_CIF",
    "KEY_UPLOADED",
    "FilterChip",
    "FilterState",
]

KEY_ISSUED = "issued"
KEY_UPLOADED = "uploaded"
KEY_DELAYED = "delayed"
KEY_NUMBER = "number"
KEY_PARTNER = "partner"
KEY_FROM_CIF = "from_cif"
KEY_TO_CIF = "to_cif"
KEY_DIRECTIONS = "directions"

_DELAYED_CHIP = "Doar întârziate"


def _needle(value: str) -> str | None:
    """A ``LIKE`` needle, or ``None`` when the box is blank.

    Stripped rather than taken literally: a field holding only spaces means
    "no filter", never "rows whose number contains two spaces".
    """
    return value.strip() or None


@dataclasses.dataclass(frozen=True)
class FilterChip:
    """One active filter, as the bar shows it: a label and how to remove it."""

    key: str
    label: str
    tooltip: str


@dataclasses.dataclass(frozen=True)
class FilterState:
    """The header's filters, as one immutable value."""

    search: str = ""
    number: str = ""
    partner: str = ""
    from_cif: str = ""
    to_cif: str = ""
    directions: frozenset[str] = ALL_DIRECTIONS
    issued: DateSpan = DateSpan()
    uploaded: DateSpan = DateSpan()
    delayed_only: bool = False

    # -- what the model queries with ------------------------------------------

    def to_filters(self, today: dt.date) -> CatalogFilters:
        """Resolve the named spans and split the direction checklist for SQL."""
        issued_from, issued_to = self.issued.resolve(today)
        uploaded_from, uploaded_to = self.uploaded.resolve(today)
        directions, show_failing = split_direction_choice(self.directions)
        return CatalogFilters(
            query=CatalogQuery(
                search=_needle(self.search),
                number=_needle(self.number),
                partner=_needle(self.partner),
                from_cif=_needle(self.from_cif),
                to_cif=_needle(self.to_cif),
                directions=directions,
                issued_from=issued_from,
                issued_to=issued_to,
                uploaded_from=uploaded_from,
                uploaded_to=uploaded_to,
            ),
            delayed_only=self.delayed_only,
            show_failing=show_failing,
        )

    # -- what the header paints ------------------------------------------------

    def active_columns(self) -> frozenset[int]:
        """Which columns' funnels should read as on."""
        active = {
            COL_ISSUED: self.issued.active,
            # The delayed flag lives in the Încărcată popover, so it lights
            # that column's funnel even when no date range is set.
            COL_UPLOADED: self.uploaded.active or self.delayed_only,
            COL_NUMBER: bool(self.number),
            COL_PARTNER: bool(self.partner),
            COL_FROM_CIF: bool(self.from_cif),
            COL_TO_CIF: bool(self.to_cif),
            COL_DIRECTION: self.directions != ALL_DIRECTIONS,
        }
        return frozenset(column for column, on in active.items() if on)

    # -- what the bar shows ----------------------------------------------------

    @property
    def any_active(self) -> bool:
        """Whether the bar has anything to show. Search does not count."""
        return bool(self.active_columns())

    def chips(self, today: dt.date) -> list[FilterChip]:
        """One chip per active filter, in the order the columns appear."""
        chips: list[FilterChip] = []
        if self.issued.active:
            chips.append(
                FilterChip(
                    KEY_ISSUED,
                    f"Emisă: {self.issued.label(today)}",
                    "Elimină filtrul pe data emiterii",
                )
            )
        if self.uploaded.active:
            chips.append(
                FilterChip(
                    KEY_UPLOADED,
                    f"Încărcată: {self.uploaded.label(today)}",
                    "Elimină filtrul pe data încărcării",
                )
            )
        if self.delayed_only:
            chips.append(
                FilterChip(KEY_DELAYED, _DELAYED_CHIP, "Arată și facturile la timp")
            )
        if self.number:
            chips.append(
                FilterChip(
                    KEY_NUMBER, f"Număr: {self.number}", "Elimină filtrul pe număr"
                )
            )
        if self.partner:
            chips.append(
                FilterChip(
                    KEY_PARTNER,
                    f"Partener: {self.partner}",
                    "Elimină filtrul pe partener",
                )
            )
        if self.from_cif:
            chips.append(
                FilterChip(
                    KEY_FROM_CIF,
                    f"De la CIF: {self.from_cif}",
                    "Elimină filtrul pe CIF-ul emitent",
                )
            )
        if self.to_cif:
            chips.append(
                FilterChip(
                    KEY_TO_CIF,
                    f"Pentru CIF: {self.to_cif}",
                    "Elimină filtrul pe CIF-ul destinatar",
                )
            )
        if self.directions != ALL_DIRECTIONS:
            shown = ", ".join(sorted(direction_label(d) for d in self.directions))
            chips.append(
                FilterChip(
                    KEY_DIRECTIONS, f"Direcție: {shown}", "Arată toate direcțiile"
                )
            )
        return chips

    # -- editing ---------------------------------------------------------------

    def without(self, key: str) -> FilterState:
        """This state with one filter removed — what a chip's ``×`` does."""
        if key == KEY_ISSUED:
            return dataclasses.replace(self, issued=DateSpan())
        if key == KEY_UPLOADED:
            return dataclasses.replace(self, uploaded=DateSpan())
        if key == KEY_DELAYED:
            return dataclasses.replace(self, delayed_only=False)
        if key == KEY_NUMBER:
            return dataclasses.replace(self, number="")
        if key == KEY_PARTNER:
            return dataclasses.replace(self, partner="")
        if key == KEY_FROM_CIF:
            return dataclasses.replace(self, from_cif="")
        if key == KEY_TO_CIF:
            return dataclasses.replace(self, to_cif="")
        if key == KEY_DIRECTIONS:
            return dataclasses.replace(self, directions=ALL_DIRECTIONS)
        raise KeyError(f"no such filter: {key!r}")

    def cleared(self) -> FilterState:
        """Every filter off — but the search box left alone, since it is visible."""
        return FilterState(search=self.search)
