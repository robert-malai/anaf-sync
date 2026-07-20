"""A two-click range calendar for the custom period filter.

Subclasses ``QCalendarWidget`` so native month navigation and locale come for
free, adding the handoff's range behaviour: the first click sets the start, the
second the end (swapped if the user picks them in reverse), then
:attr:`range_selected` fires. Endpoints paint in the accent colour, days between
them in accent-soft. The selection state machine is pure enough to unit-test
without a display.
"""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QDate, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCalendarWidget, QWidget

from .theme import LIGHT, Theme

__all__ = ["RangeCalendar"]


class RangeCalendar(QCalendarWidget):
    """A calendar whose two clicks define an inclusive date range."""

    #: Emitted with ``(start: date, end: date)`` once both ends are chosen.
    range_selected = Signal(object, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start: dt.date | None = None
        self._end: dt.date | None = None
        self._theme: Theme = LIGHT
        self.setGridVisible(False)
        self.clicked.connect(self._on_clicked)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.updateCells()

    def selected_range(self) -> tuple[dt.date | None, dt.date | None]:
        return self._start, self._end

    def set_range(self, start: dt.date | None, end: dt.date | None) -> None:
        self._start, self._end = start, end
        self.updateCells()

    def _on_clicked(self, qdate: QDate) -> None:
        self._pick(_to_date(qdate))

    def _pick(self, day: dt.date) -> None:
        """Advance the range state machine by one click (pure; testable)."""
        if self._start is None or self._end is not None:
            # Fresh range: this click is the new start.
            self._start, self._end = day, None
        else:
            start, end = self._start, day
            if end < start:
                start, end = end, start  # clicks in reverse: swap
            self._start, self._end = start, end
            self.range_selected.emit(start, end)
        self.updateCells()

    def paintCell(self, painter: QPainter, rect: QRect, qdate: QDate) -> None:
        day = _to_date(qdate)
        theme = self._theme
        if self._start is not None and (day == self._start or day == self._end):
            painter.fillRect(rect, QColor(theme.accent))
            painter.setPen(QColor(theme.on_accent))
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), str(day.day))
            return
        if (
            self._start is not None
            and self._end is not None
            and self._start < day < self._end
        ):
            painter.fillRect(rect, QColor(theme.accent_soft_bg))
        super().paintCell(painter, rect, qdate)


def _to_date(qdate: QDate) -> dt.date:
    return dt.date(qdate.year(), qdate.month(), qdate.day())
