"""The Facturi header: the label sorts, the ▽ filters, the boundary resizes.

Three hit targets in one section. ``QHeaderView`` hosts no child widgets, so
the funnel is *painted* into each section and hit-tested in
:meth:`CatalogHeader.mousePressEvent`; it sits one margin clear of the resize
handle so a click near the boundary still means "resize".

Both marks — the sort caret and the funnel — are drawn as polygons rather than
typed as ``▲``/``▽``. Those glyphs are missing from enough UI fonts that on
some Windows and Linux desktops the header would render two tofu boxes, and a
cross-platform tray cannot ship a control that depends on a font's coverage.

Sorting is owned outright rather than layered on Qt's. Neither the view's
``setSortingEnabled`` nor ``sectionsClickable`` is used, because both hand the
gesture to ``QHeaderView`` — which flips its own sort indicator inside
``mouseReleaseEvent`` *before* it emits ``sectionClicked``. A handler reading
the indicator at that point sees the already-flipped state, so "click again to
reverse" can never reverse, and a click on an unsortable section still leaves
the indicator sitting on it. Deciding the sort on the release instead, from the
section captured on the press, is what makes both behave; the result is still
announced through the standard ``sortIndicatorChanged`` signal, so the window
wires the model to Qt's own vocabulary rather than a bespoke one — and exactly
once per click, rather than once for Qt's guess and once for the correction.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPolygonF
from PySide6.QtWidgets import QApplication, QHeaderView, QWidget

from .models import COL_TOTAL, COLUMN_SORT_KEYS
from .theme import LIGHT, Theme

__all__ = ["FUNNEL_MARGIN", "MARKS_WIDTH", "MARK_BOX", "CatalogHeader"]

#: Both marks are drawn inside a box this wide, side by side.
MARK_BOX = 9
_MARK_GAP = 5
#: How far the funnel stays clear of the section boundary. The resize handle
#: Qt reserves there is a few pixels wide either side; overlapping it would
#: make "grab the edge to resize" fail on the pixel a user aims at most.
FUNNEL_MARGIN = 6

#: How much of a section the two marks claim. The window pads the header text
#: by this much on the right, so a label can never run under them, and sizes
#: its fixed columns to fit label + marks as well as their widest value.
MARKS_WIDTH = MARK_BOX * 2 + _MARK_GAP + FUNNEL_MARGIN

#: Every column carries a filter except Total: an amount range is a form, not
#: a popover. Its bare header is also what tells the eye the ▽ marks a
#: per-column control rather than decoration.
_UNFILTERABLE = frozenset({COL_TOTAL})

#: "No section" — what ``logicalIndexAt`` returns past the last one.
_NOTHING = -1


class CatalogHeader(QHeaderView):
    """A horizontal header whose sections sort, filter, and resize."""

    #: A section's funnel was clicked; the window opens that column's popover.
    filter_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._theme: Theme = LIGHT
        self._filtered: frozenset[int] = frozenset()
        self._hovered_funnel = _NOTHING
        self._pressed_section = _NOTHING
        self._press_origin: QPoint | None = None
        # Not clickable: that is the switch that hands the gesture — and the
        # indicator flip — to QHeaderView. Resize handles are unaffected.
        self.setSectionsClickable(False)
        # Three hit targets share a section, so each has to answer the pointer
        # on its own: the section highlights through QSS ``:hover`` (which
        # needs WA_Hover), and the funnel repaints itself from the tracked
        # position below.
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # Qt's own indicator is right-aligned and would land on top of the
        # funnel; this class paints the caret itself, in its own slot.
        self.setSortIndicatorShown(False)
        self.setHighlightSections(False)

    # -- state ----------------------------------------------------------------

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.viewport().update()

    def set_filtered(self, sections: frozenset[int]) -> None:
        """Which columns currently filter, so their funnel reads as active."""
        if sections != self._filtered:
            self._filtered = sections
            self.viewport().update()

    def funnel_is_active(self, section: int) -> bool:
        """Whether this column's funnel is painted as filtering."""
        return section in self._filtered

    def is_sortable(self, section: int) -> bool:
        return section in COLUMN_SORT_KEYS

    def is_filterable(self, section: int) -> bool:
        return 0 <= section < self.count() and section not in _UNFILTERABLE

    # -- geometry -------------------------------------------------------------

    def funnel_rect(self, section: int) -> QRect:
        """Where this section's funnel is painted, in viewport coordinates."""
        left = self.sectionViewportPosition(section)
        width = self.sectionSize(section)
        return self._mark_rect(QRect(left, 0, width, self.height()), outer=True)

    def _mark_rect(self, section_rect: QRect, *, outer: bool) -> QRect:
        """The funnel's box (``outer``) or the caret's, just inside it."""
        right = section_rect.right() - FUNNEL_MARGIN
        if not outer:
            right -= MARK_BOX + _MARK_GAP
        top = section_rect.center().y() - MARK_BOX // 2
        return QRect(right - MARK_BOX, top, MARK_BOX, MARK_BOX)

    # -- painting -------------------------------------------------------------

    def paintSection(  # noqa: N802 — Qt override
        self, painter: QPainter, rect: QRect, logicalIndex: int
    ) -> None:
        super().paintSection(painter, rect, logicalIndex)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.sortIndicatorSection() == logicalIndex and self.is_sortable(
            logicalIndex
        ):
            # A column with no funnel has the outer slot free, so the caret
            # takes it — which is what lets Total's right-aligned label stop at
            # the same inset as the values beneath it instead of running under
            # its own indicator.
            self._paint_caret(
                painter,
                self._mark_rect(rect, outer=not self.is_filterable(logicalIndex)),
            )
        if self.is_filterable(logicalIndex):
            self._paint_funnel(
                painter,
                self._mark_rect(rect, outer=True),
                active=logicalIndex in self._filtered,
                hovered=logicalIndex == self._hovered_funnel,
            )
        painter.restore()

    def _paint_caret(self, painter: QPainter, box: QRect) -> None:
        """A solid triangle pointing the way the column is sorted."""
        up = self.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
        mid = box.center().x() + 1
        top, bottom = box.top() + 2, box.bottom() - 1
        tip, base = (top, bottom) if up else (bottom, top)
        triangle = QPolygonF(
            [
                QPoint(mid, tip),
                QPoint(box.left(), base),
                QPoint(box.right(), base),
            ]
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._theme.accent))
        painter.drawPolygon(triangle)

    def _paint_funnel(
        self, painter: QPainter, box: QRect, *, active: bool, hovered: bool = False
    ) -> None:
        """A funnel: outlined and faint when idle, solid accent when on.

        A funnel and not a second triangle — the sort caret is already one, and
        at nine pixels two triangles side by side read as one control with a
        stutter. Always drawn, never hover-only: a filter that appears under
        the pointer is one nobody finds.
        """
        mid = box.center().x() + 1
        top, bottom = box.top() + 1, box.bottom()
        waist = top + 4
        stem = 1.5
        funnel = QPolygonF(
            [
                QPointF(box.left(), top),
                QPointF(box.right() + 1, top),
                QPointF(mid + stem, waist),
                QPointF(mid + stem, bottom),
                QPointF(mid - stem, bottom),
                QPointF(mid - stem, waist),
            ]
        )
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._theme.accent))
        else:
            # Under the pointer it goes accent at full strength: that change is
            # what tells a first-time reader the ▽ is its own control and not
            # part of the label they were about to click to sort.
            colour = QColor(self._theme.accent if hovered else self._theme.faint)
            if not hovered:
                colour.setAlphaF(0.6)
            painter.setPen(colour)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(funnel)

    # -- interaction ----------------------------------------------------------

    def funnel_at(self, point: QPoint) -> int:
        """The section whose funnel covers ``point``, or ``-1`` for none."""
        section = self.logicalIndexAt(point)
        if self.is_filterable(section) and self.funnel_rect(section).contains(point):
            return section
        return _NOTHING

    @property
    def hovered_funnel(self) -> int:
        """The section whose funnel the pointer is over, or ``-1``."""
        return self._hovered_funnel

    def _hover(self, section: int) -> None:
        if section != self._hovered_funnel:
            self._hovered_funnel = section
            self.viewport().update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802 — Qt override
        self._hover(self.funnel_at(e.position().toPoint()))
        super().mouseMoveEvent(e)

    def leaveEvent(self, e: QEvent) -> None:  # noqa: N802 — Qt override
        self._hover(_NOTHING)
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802 — Qt override
        point = e.position().toPoint()
        section = self.funnel_at(point)
        if section != _NOTHING:
            # Swallowed on purpose: letting this reach QHeaderView would sort
            # the column as well as open its filter.
            self.filter_requested.emit(section)
            return
        # Both remembered so the release can tell a click apart from the end
        # of a drag — which, on a boundary, is a resize and must not re-sort
        # the neighbouring column as well.
        self._pressed_section = self.logicalIndexAt(point)
        self._press_origin = point
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802 — Qt override
        pressed, self._pressed_section = self._pressed_section, _NOTHING
        origin, self._press_origin = self._press_origin, None
        point = e.position().toPoint()
        super().mouseReleaseEvent(e)
        if pressed != _NOTHING and pressed == self.logicalIndexAt(point):
            self._toggle_sort_if_clicked(pressed, origin, point)

    def _toggle_sort_if_clicked(
        self, section: int, origin: QPoint | None, released: QPoint
    ) -> None:
        """Sort only if the pointer stayed put — a drag was a resize."""
        if origin is None:
            return
        moved = (released - origin).manhattanLength()
        if moved <= QApplication.startDragDistance():
            self._toggle_sort(section)

    def _toggle_sort(self, section: int) -> None:
        """Sort by ``section``, reversing if it is already the sorted one."""
        if not self.is_sortable(section):
            return  # Direcție: there is no order to move the indicator onto
        already = self.sortIndicatorSection() == section
        descending = self.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        # A first click on a new column sorts it descending — newest, largest,
        # Z-first — because that is what the catalog's default order means and
        # what a reader clicking "Emisă" is asking for.
        ascending = already and descending
        self.setSortIndicator(
            section,
            Qt.SortOrder.AscendingOrder if ascending else Qt.SortOrder.DescendingOrder,
        )
