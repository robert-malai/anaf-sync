"""Table cell painting: direction pills, failing/delayed stripes, amber dates.

A ``QStyledItemDelegate`` that reads the custom roles :class:`CatalogModel`
exposes (``FailingRole`` / ``DelayedRole`` / ``DirectionRole``) and paints the
handoff's row treatments — a 3 px inset stripe (red for failing, amber for
delayed), an amber-600 Data cell on delayed rows, and the direction pill. All
colours come from the active :class:`Theme`; none are hard-coded here.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .format import direction_label
from .models import CatalogModel
from .theme import LIGHT, RADIUS_PILL, Theme

__all__ = ["CatalogDelegate"]

#: Qt hands delegate methods either index flavour; accept both.
_Index = QModelIndex | QPersistentModelIndex

_STRIPE_WIDTH = 3
_PAD_X = 14


class CatalogDelegate(QStyledItemDelegate):
    """Paints direction pills and failing/delayed row treatments."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._theme: Theme = LIGHT

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: _Index,
    ) -> None:
        theme = self._theme
        failing = bool(index.data(CatalogModel.FailingRole))
        delayed = bool(index.data(CatalogModel.DelayedRole))
        col = index.column()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_background(painter, option, theme)

        if col == 3:
            self._paint_pill(painter, option, index, theme)
        elif col == 0 and delayed:
            self._paint_text(painter, option, index, theme.amber, bold=True)
        elif failing:
            self._paint_text(painter, option, index, theme.red)
        else:
            self._paint_text(painter, option, index, theme.text)

        self._paint_stripe(painter, option, failing, delayed, theme)
        painter.restore()

    # -- pieces ---------------------------------------------------------------

    def _paint_background(
        self, painter: QPainter, option: QStyleOptionViewItem, theme: Theme
    ) -> None:
        state = option.state
        if state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(theme.row_selected))
        elif state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(option.rect, QColor(theme.row_hover))

    def _paint_text(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: _Index,
        color: str,
        *,
        bold: bool = False,
    ) -> None:
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return
        painter.setPen(QPen(QColor(color)))
        font = QFont(option.font)
        font.setBold(bold)
        painter.setFont(font)
        rect = option.rect.adjusted(_PAD_X, 0, -_PAD_X, 0)
        align = index.data(Qt.ItemDataRole.TextAlignmentRole)
        flags = (
            int(align)
            if align
            else int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        )
        painter.drawText(rect, flags, str(text))

    def _paint_pill(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: _Index,
        theme: Theme,
    ) -> None:
        direction = index.data(CatalogModel.DirectionRole)
        label, bg, fg, border = self._pill_style(direction, theme)
        if not label:
            return
        font = QFont(option.font)
        font.setPointSizeF(max(1.0, option.font.pointSizeF() - 1))
        font.setBold(True)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(label)
        pill_w = text_w + 16
        pill_h = metrics.height() + 4
        left = option.rect.left() + _PAD_X
        top = option.rect.center().y() - pill_h / 2
        pill = QRectF(left, top, pill_w, pill_h)

        painter.setPen(QPen(QColor(border)) if border else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(pill, RADIUS_PILL, RADIUS_PILL)
        painter.setPen(QPen(QColor(fg)))
        painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), label)

    def _pill_style(
        self, direction: object, theme: Theme
    ) -> tuple[str, str, str, str | None]:
        label = direction_label(str(direction))
        if direction == "received":
            return label, theme.accent_soft_bg, theme.accent, None
        if direction == "sent":
            return label, theme.mono_chip_bg, theme.muted, theme.border
        if direction == "failing":
            return label, theme.red_bg, theme.red, None
        return "", theme.mono_chip_bg, theme.muted, None

    def _paint_stripe(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        failing: bool,
        delayed: bool,
        theme: Theme,
    ) -> None:
        if not (failing or delayed):
            return
        color = theme.red if failing else theme.amber
        stripe = option.rect
        painter.fillRect(
            stripe.left(), stripe.top(), _STRIPE_WIDTH, stripe.height(), QColor(color)
        )
