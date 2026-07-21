"""Tray icons painted at runtime with ``QPainter`` — no image assets to ship.

Each icon is a small document glyph with a status dot (green / amber / red)
overlaid; per the handoff, the dot alone must convey state. The glyph is drawn
in the active theme's text colour, which — because the theme tracks the OS
colour scheme — reads correctly on both a light and a dark menu bar, so the
coloured dot never has to invert.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

from .theme import LIGHT, Theme, status_color

__all__ = ["status_icon"]

HealthState = str

#: Rendered sizes; the OS picks the one that fits the tray/menu-bar slot.
_SIZES = (16, 22, 32)


def status_icon(state: HealthState, *, theme: Theme | None = None) -> QIcon:
    """A multi-size :class:`QIcon`: document glyph + coloured status dot."""
    theme = theme or LIGHT
    icon = QIcon()
    for size in _SIZES:
        icon.addPixmap(_render(state, size, theme))
    return icon


def _render(state: str, size: int, theme: Theme) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        _paint_document(painter, size, QColor(theme.text))
        _paint_dot(painter, size, QColor(status_color(theme, state)))  # type: ignore[arg-type]
    finally:
        painter.end()
    return pixmap


def _paint_document(painter: QPainter, size: int, color: QColor) -> None:
    """A page with a folded top-right corner, as a thin outline."""
    pen_width = max(1.0, size / 16.0)
    painter.setPen(QColor(color))
    pen = painter.pen()
    pen.setWidthF(pen_width)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    left = size * 0.24
    right = size * 0.76
    top = size * 0.12
    bottom = size * 0.88
    fold = size * 0.20  # size of the dog-eared corner

    page = QPolygonF(
        [
            QPointF(left, top),
            QPointF(right - fold, top),
            QPointF(right, top + fold),
            QPointF(right, bottom),
            QPointF(left, bottom),
        ]
    )
    painter.drawPolygon(page)
    # The fold itself.
    painter.drawPolyline(
        QPolygonF(
            [
                QPointF(right - fold, top),
                QPointF(right - fold, top + fold),
                QPointF(right, top + fold),
            ]
        )
    )


def _paint_dot(painter: QPainter, size: int, color: QColor) -> None:
    """The status dot, bottom-right, with a transparent gap around it."""
    radius = size * 0.26
    cx = size * 0.72
    cy = size * 0.72
    # Punch a transparent halo so the dot reads against the glyph edge.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(Qt.GlobalColor.black)
    halo = radius * 1.35
    painter.drawEllipse(QPointF(cx, cy), halo, halo)

    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.setBrush(color)
    painter.drawEllipse(QPointF(cx, cy), radius, radius)
