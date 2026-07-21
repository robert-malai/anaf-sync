"""The *Fișiere salvate* grid: equal columns that re-flow 3-up ⇄ 5-up.

A ``QLayout`` subclass rather than ``resizeEvent`` arithmetic, so the Setări
form keeps the one rule its elastic layout is built on (DESIGN.md §10). The
column count is derived from the width the layout is handed, and the allowed
counts are deliberately **only 3 and 5**: five artifacts in four columns strand
one card alone on a second row, and 3 + 2 and 5 are the only clean partitions
of five. :func:`column_count` is pure, so the rule is unit-tested without Qt
geometry.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget

__all__ = ["ArtifactGrid", "MIN_COLUMN_WIDTH", "SPACING", "column_count"]

#: Below this per-card width the descriptions wrap to three lines and the
#: one-row layout is *worse* than the 3-up it would replace — so the switch
#: fires when 5-up is an improvement, not as soon as it geometrically fits.
MIN_COLUMN_WIDTH = 170
SPACING = 8

_NARROW_COLUMNS = 3
_WIDE_COLUMNS = 5


def column_count(width: int) -> int:
    """``5`` once every card clears :data:`MIN_COLUMN_WIDTH`, else ``3``.

    Four is never returned: it would leave a single card orphaned on the
    second row.
    """
    needed = _WIDE_COLUMNS * MIN_COLUMN_WIDTH + SPACING * (_WIDE_COLUMNS - 1)
    return _WIDE_COLUMNS if width >= needed else _NARROW_COLUMNS


class ArtifactGrid(QLayout):
    """Lays its items out in equal columns, 3-up or 5-up by available width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(SPACING)

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
        item_width = max(
            (item.minimumSize().width() for item in self._items), default=0
        )
        width = item_width * _NARROW_COLUMNS + SPACING * (_NARROW_COLUMNS - 1)
        return QSize(width, self.heightForWidth(max(width, 1)))

    # -- layout ---------------------------------------------------------------

    def _layout(self, rect: QRect, *, apply: bool) -> int:
        """Place (or just measure) the items; returns the total height."""
        if not self._items:
            return 0
        columns = column_count(rect.width())
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


def stretchable(widget: QWidget) -> QWidget:
    """Let ``widget`` be widened by the grid instead of sitting at its hint."""
    widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return widget
