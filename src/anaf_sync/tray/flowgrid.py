"""Equal-column grids that re-flow on the width they are handed.

A ``QLayout`` subclass rather than ``resizeEvent`` arithmetic, so the Setări
form keeps the one rule its elastic layout is built on (DESIGN.md §10). Two
users, both keyed to :data:`WIDE_BREAKPOINT` so the window has **one** reflow
moment across its 760–1200 range rather than two competing ones:
:class:`ArtifactGrid` for *Fișiere salvate* (3-up ⇄ 5-up) and the variable
reference panel's group columns (1-up ⇄ 3-up, see :mod:`template_help`).

The artifact counts are deliberately **only 3 and 5**: five artifacts in four
columns strand one card alone on a second row, and 3 + 2 and 5 are the only
clean partitions of five. :func:`column_count` and :func:`group_column_count`
are pure, so both rules are unit-tested without Qt geometry.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget

__all__ = [
    "MIN_COLUMN_WIDTH",
    "SPACING",
    "WIDE_BREAKPOINT",
    "ArtifactGrid",
    "ColumnGrid",
    "GroupGrid",
    "clear_layout",
    "column_count",
    "group_column_count",
]

#: Below this per-card width the descriptions wrap to three lines and the
#: one-row layout is *worse* than the 3-up it would replace — so the switch
#: fires when 5-up is an improvement, not as soon as it geometrically fits.
MIN_COLUMN_WIDTH = 170
SPACING = 8

_NARROW_COLUMNS = 3
_WIDE_COLUMNS = 5

#: The single field-column width at which the Setări form re-flows (882px).
#: Derived from the artifact rule, then *reused* by the variable panel — the
#: number matters less than there being only one of it.
WIDE_BREAKPOINT = _WIDE_COLUMNS * MIN_COLUMN_WIDTH + SPACING * (_WIDE_COLUMNS - 1)


def column_count(width: int) -> int:
    """``5`` once every card clears :data:`MIN_COLUMN_WIDTH`, else ``3``.

    Four is never returned: it would leave a single card orphaned on the
    second row.
    """
    return _WIDE_COLUMNS if width >= WIDE_BREAKPOINT else _NARROW_COLUMNS


def group_column_count(width: int) -> int:
    """``3`` for the variable panel's groups past the breakpoint, else ``1``.

    Two is never returned: three groups in two columns strand *Mesaj SPV*
    alone on a second row, the same orphan the artifact rule avoids.
    """
    return 3 if width >= WIDE_BREAKPOINT else 1


class ColumnGrid(QLayout):
    """Lays its items out in equal columns; subclasses choose the count."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(SPACING)

    def columns_for(self, width: int) -> int:
        """How many columns to use at ``width`` — the whole reflow rule."""
        raise NotImplementedError

    def widest_item(self) -> int:
        """The widest item's minimum width, for rules that pack by measurement.

        Measured rather than assumed: a hard-coded column width is wrong on the
        first platform whose font metrics differ, which is the same trap the
        handoff calls out for the Facturi table's column floors.
        """
        return max((item.minimumSize().width() for item in self._items), default=0)

    # -- the QLayout contract -------------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 — Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 — Qt override
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 — Qt override
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 — Qt override
        # Width is what changes the layout; height is whatever the rows need.
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — Qt override
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 — Qt override
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 — Qt override
        item_width = self.widest_item()
        narrow = self.columns_for(0)
        width = item_width * narrow + SPACING * (narrow - 1)
        return QSize(width, self.heightForWidth(max(width, 1)))

    # -- layout ---------------------------------------------------------------

    def _layout(self, rect: QRect, *, apply: bool) -> int:
        """Place (or just measure) the items; returns the total height."""
        if not self._items:
            return 0
        columns = self.columns_for(rect.width())
        column_width = max(1, (rect.width() - SPACING * (columns - 1)) // columns)
        y = rect.y()
        total = 0
        for start in range(0, len(self._items), columns):
            row = self._items[start : start + columns]
            row_height = max(_item_height(item, column_width) for item in row)
            if apply:
                for offset, item in enumerate(row):
                    x = rect.x() + offset * (column_width + SPACING)
                    item.setGeometry(QRect(x, y, column_width, row_height))
            y += row_height + SPACING
            total += row_height + SPACING
        return max(0, total - SPACING)


def _item_height(item: QLayoutItem, width: int) -> int:
    """The height this item needs at ``width`` — wrapping labels grow here."""
    widget = item.widget()
    if widget is not None and widget.hasHeightForWidth():
        return int(widget.heightForWidth(width))
    return int(item.sizeHint().height())


class ArtifactGrid(ColumnGrid):
    """The *Fișiere salvate* grid: 3-up or 5-up by available width."""

    def columns_for(self, width: int) -> int:
        return column_count(width)


class GroupGrid(ColumnGrid):
    """The variable panel's group columns: 1-up or 3-up, same breakpoint."""

    def columns_for(self, width: int) -> int:
        return group_column_count(width)


def clear_layout(layout: QLayout) -> None:
    """Remove and delete every widget in ``layout`` (for rebuild-in-place views)."""
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
