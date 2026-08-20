"""The active-filter bar under the search field.

A filter shut inside a header popover is invisible, and a catalog missing rows
for a reason the reader cannot see is worse than no filtering at all. Every
active filter is echoed here as a removable label, so "why is this invoice not
in the list?" always has an answer on screen.

The band has no height when nothing is filtered, which makes the default view
*less* chrome than the toolbar chips it replaces, not more. It wraps to a
second line rather than eliding: a hidden chip would defeat its own purpose.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from .filters import FilterChip
from .flowgrid import WrapRow, clear_layout
from .theme import LIGHT, Theme

__all__ = ["CLEAR_ALL_LABEL", "TITLE", "ActiveFilterBar"]

TITLE = "Filtre active"
CLEAR_ALL_LABEL = "Șterge toate filtrele"
_REMOVE_GLYPH = "×"


class ActiveFilterBar(QWidget):
    """Removable labels for the filters currently narrowing the catalog."""

    #: One chip's ``×`` was clicked, with the :mod:`filters` key it carries.
    chip_removed = Signal(str)
    #: The "Șterge toate filtrele" link was clicked.
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme: Theme = LIGHT
        self._chips: Sequence[FilterChip] = ()
        self._layout = WrapRow(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.setVisible(False)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(filter_bar_qss(theme))

    def set_chips(self, chips: Sequence[FilterChip]) -> None:
        """Rebuild the bar; hide it entirely when there is nothing to show."""
        self._chips = chips
        clear_layout(self._layout)
        self.setVisible(bool(chips))
        if not chips:
            return
        title = QLabel(TITLE)
        title.setObjectName("filterBarTitle")
        self._layout.addWidget(title)
        for chip in chips:
            self._layout.addWidget(self._build_chip(chip))
        self._layout.addWidget(self._build_clear_all())

    def _build_chip(self, chip: FilterChip) -> QWidget:
        holder = QWidget()
        holder.setObjectName("filterChip")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(9, 2, 4, 2)
        layout.setSpacing(6)
        label = QLabel(chip.label)
        label.setObjectName("filterChipLabel")
        remove = QToolButton()
        remove.setObjectName("filterChipRemove")
        remove.setText(_REMOVE_GLYPH)
        remove.setToolTip(chip.tooltip)
        remove.setCursor(Qt.CursorShape.ArrowCursor)
        remove.clicked.connect(lambda: self.chip_removed.emit(chip.key))
        layout.addWidget(label)
        layout.addWidget(remove)
        return holder

    def _build_clear_all(self) -> QWidget:
        link = QToolButton()
        link.setObjectName("clearAllLink")
        link.setText(CLEAR_ALL_LABEL)
        link.clicked.connect(self.cleared)
        return link


def filter_bar_qss(theme: Theme) -> str:
    return f"""
#filterBarTitle {{ color: {theme.faint}; font-size: 11px; }}
#filterChip {{
    background-color: {theme.accent_soft_bg};
    border: 1px solid {theme.accent};
    border-radius: 5px;
}}
#filterChipLabel {{ color: {theme.accent}; background-color: transparent; }}
#filterChipRemove {{
    background-color: transparent;
    border: none;
    color: {theme.accent};
    padding: 0px 2px;
}}
#clearAllLink {{
    background-color: transparent;
    border: none;
    color: {theme.muted};
    font-size: 11px;
    padding: 2px 4px;
    text-decoration: underline;
}}
#clearAllLink:hover {{ color: {theme.accent}; }}
"""
