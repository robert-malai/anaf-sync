"""The four filter popovers a column header's ▽ opens.

One kind per shape of question, not one per column:

- :class:`DateFilterPopup` — Emisă and Încărcată. Named spans
  (:mod:`anaf_sync.tray.period`) with a calendar behind "Personalizat…", plus
  the "doar întârziate" checkbox on Încărcată. Lateness is a fact about the
  *upload* date, so it belongs to the column whose cell already turns amber,
  not to a general-purpose "probleme" bucket.
- :class:`TextFilterPopup` — Număr and Partener. One "conține…" field.
- :class:`CifFilterPopup` — De la CIF and Pentru CIF. Also a "conține…" field,
  deliberately *not* a checklist: only the followed CIFs are a bounded set, and
  the counterparty side is every company that ever invoiced you. The followed
  ones ride above it as one-click shortcuts, which keeps the question the
  column was added for — "which of my entities is this?" — at a single click.
- :class:`ChecklistFilterPopup` — Direcție, whose three values *are* bounded.

Every popup applies live and ends in a "Șterge filtrul" link: there is no OK
button to forget to press. They are ``Qt.Popup`` windows, so clicking anywhere
outside closes them, and they clamp themselves onto the screen rather than
being anchored by hand — a column near the right edge would otherwise open a
popover half off the window.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from PySide6.QtCore import QDate, QRect, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .calendar import RangeCalendar, to_date
from .period import ALL, CUSTOM, MODES, DateSpan, month_end
from .theme import LIGHT, RADIUS_BUTTON, Theme

__all__ = [
    "CLEAR_LABEL",
    "DELAYED_LABEL",
    "ChecklistFilterPopup",
    "CifFilterPopup",
    "DateFilterPopup",
    "FilterPopup",
    "TextFilterPopup",
]

CLEAR_LABEL = "Șterge filtrul"
DELAYED_LABEL = "Doar declarate cu întârziere"
_OWN_CIFS_LABEL = "CIF-urile tale"
_DATE_FORMAT = "dd.MM.yyyy"
_GAP = 4


class FilterPopup(QWidget):
    """Base for the four popovers: a themed ``Qt.Popup`` that applies live."""

    #: The filter changed. Emitted per keystroke and per click — the window
    #: re-queries, which is cheap and is what "no OK button" costs.
    changed = Signal()

    def __init__(self, theme: Theme = LIGHT, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self._theme = theme
        self.setObjectName("filterPopup")
        self.setStyleSheet(popup_qss(theme))
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(_GAP)

    # -- shared pieces --------------------------------------------------------

    def _add_clear_link(self) -> None:
        link = QToolButton()
        link.setObjectName("clearLink")
        link.setText(CLEAR_LABEL)
        link.clicked.connect(self._clear)
        self._layout.addWidget(link, 0, Qt.AlignmentFlag.AlignLeft)

    def _add_separator(self) -> None:
        rule = QFrame()
        rule.setObjectName("popupRule")
        rule.setFrameShape(QFrame.Shape.HLine)
        self._layout.addWidget(rule)

    def _clear(self) -> None:
        """Reset this popup's filter. Subclasses do the resetting."""
        raise NotImplementedError

    # -- placement ------------------------------------------------------------

    def open_under(self, anchor: QRect) -> None:
        """Show below ``anchor`` (a header section, in global coordinates).

        Clamped onto the anchor's screen rather than anchored by hand: the two
        right-hand columns would otherwise open a popover hanging off the
        window, and a low window would open one below the desktop.
        """
        self.adjustSize()
        screen = QApplication.screenAt(anchor.center())
        bounds = (screen or QApplication.primaryScreen()).availableGeometry()
        x = min(anchor.left(), bounds.right() - self.width())
        y = anchor.bottom() + _GAP
        if y + self.height() > bounds.bottom():
            y = max(bounds.top(), anchor.top() - self.height() - _GAP)
        self.move(max(bounds.left(), x), y)
        self.show()


class TextFilterPopup(FilterPopup):
    """A single "conține…" field over one text column."""

    def __init__(
        self,
        *,
        value: str,
        placeholder: str,
        theme: Theme = LIGHT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self._field = QLineEdit(value)
        self._field.setPlaceholderText(placeholder)
        self._field.setMinimumWidth(176)
        self._field.textChanged.connect(lambda _text: self.changed.emit())
        self._layout.addWidget(self._field)
        self._add_clear_link()

    @property
    def value(self) -> str:
        return self._field.text().strip()

    def _clear(self) -> None:
        self._field.clear()


class CifFilterPopup(FilterPopup):
    """A "conține…" field with the followed CIFs as one-click shortcuts."""

    def __init__(
        self,
        *,
        value: str,
        own_cifs: Sequence[tuple[str, int]],
        theme: Theme = LIGHT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self._field = QLineEdit(value)
        self._field.setPlaceholderText("CIF-ul conține…")
        self._field.setMinimumWidth(176)
        self._field.textChanged.connect(lambda _text: self.changed.emit())
        self._layout.addWidget(self._field)
        if own_cifs:
            caption = QLabel(_OWN_CIFS_LABEL)
            caption.setObjectName("popupCaption")
            self._layout.addWidget(caption)
            for cif, count in own_cifs:
                self._layout.addWidget(self._shortcut(cif, count))
        self._add_clear_link()

    def _shortcut(self, cif: str, count: int) -> QWidget:
        row = QToolButton()
        row.setObjectName("cifShortcut")
        row.setText(f"{cif}   {count}")
        row.setToolTip(f"Filtrează după {cif}")
        # A second click on the same shortcut clears it, so the one-click case
        # is also a one-click undo.
        row.clicked.connect(lambda: self._pick(cif))
        return row

    def _pick(self, cif: str) -> None:
        self._field.setText("" if self.value == cif else cif)

    @property
    def value(self) -> str:
        return self._field.text().strip()

    def _clear(self) -> None:
        self._field.clear()


class ChecklistFilterPopup(FilterPopup):
    """One checkbox per value of a bounded column, with its archive count."""

    def __init__(
        self,
        *,
        options: Sequence[tuple[str, str, int]],  # (value, label, count)
        checked: frozenset[str],
        theme: Theme = LIGHT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self._boxes: dict[str, QCheckBox] = {}
        for value, label, count in options:
            self._layout.addLayout(self._row(value, label, count, value in checked))
        self._add_separator()
        self._add_clear_link()

    def _row(self, value: str, label: str, count: int, on: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        box = QCheckBox(label)
        box.setChecked(on)
        box.clicked.connect(lambda _on, v=value: self._toggled(v))
        self._boxes[value] = box
        tally = QLabel(str(count))
        tally.setObjectName("popupCount")
        row.addWidget(box)
        row.addStretch(1)
        row.addWidget(tally)
        return row

    def _toggled(self, value: str) -> None:
        """Apply, unless this would leave nothing checked.

        Emptying the list would show an empty table whose only way back is
        "Șterge filtrul" — the same refusal the Setări CIF list makes for its
        last chip. The box springs back rather than the change being silently
        dropped, so the refusal is visible.
        """
        if not self.checked:
            self._boxes[value].setChecked(True)
            return
        self.changed.emit()

    @property
    def checked(self) -> frozenset[str]:
        return frozenset(v for v, box in self._boxes.items() if box.isChecked())

    def _clear(self) -> None:
        for box in self._boxes.values():
            box.setChecked(True)
        self.changed.emit()


class DateFilterPopup(FilterPopup):
    """Named spans, a calendar behind "Personalizat…", and the delayed flag."""

    def __init__(
        self,
        *,
        span: DateSpan,
        today: dt.date,
        with_delayed: bool = False,
        delayed: bool = False,
        theme: Theme = LIGHT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(theme, parent)
        self._today = today
        # The chosen mode is tracked here, not read back off the radios: the
        # radio has already flipped by the time its `clicked` handler runs, so
        # a handler that asks "which is checked?" cannot see what it replaced.
        self._mode = span.mode
        self._modes = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}
        for mode in MODES:
            button = QRadioButton(mode)
            button.setChecked(mode == span.mode)
            button.clicked.connect(lambda _on, m=mode: self._pick_mode(m))
            self._modes.addButton(button)
            self._buttons[mode] = button
            self._layout.addWidget(button)

        start, end = span.resolve(today)
        self._range = self._build_range(start or today, end or today)
        self._layout.addWidget(self._range)
        self._calendar = RangeCalendar()
        self._calendar.set_theme(theme)
        self._calendar.range_selected.connect(self._on_range_picked)
        self._layout.addWidget(self._calendar)

        self._delayed: QCheckBox | None = None
        if with_delayed:
            self._add_separator()
            self._delayed = QCheckBox(DELAYED_LABEL)
            self._delayed.setChecked(delayed)
            self._delayed.clicked.connect(lambda _on: self.changed.emit())
            self._layout.addWidget(self._delayed)

        self._add_clear_link()
        self._sync_custom()

    def _build_range(self, start: dt.date, end: dt.date) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_GAP)
        self._from = QDateEdit(QDate(start.year, start.month, start.day))
        self._to = QDateEdit(QDate(end.year, end.month, end.day))
        for edit in (self._from, self._to):
            edit.setDisplayFormat(_DATE_FORMAT)
            edit.setCalendarPopup(False)
            edit.dateChanged.connect(lambda _d: self._on_edited())
        layout.addWidget(self._from)
        layout.addWidget(QLabel("–"))
        layout.addWidget(self._to)
        layout.addStretch(1)
        return row

    # -- state ----------------------------------------------------------------

    @property
    def span(self) -> DateSpan:
        return self._span_for(self._mode)

    def _span_for(self, mode: str) -> DateSpan:
        if mode != CUSTOM:
            return DateSpan(mode=mode)
        return DateSpan(CUSTOM, to_date(self._from.date()), to_date(self._to.date()))

    @property
    def delayed(self) -> bool:
        return self._delayed is not None and self._delayed.isChecked()

    # -- interaction ----------------------------------------------------------

    def _pick_mode(self, mode: str) -> None:
        if mode == CUSTOM:
            # Seed the fields from the span the user was just looking at — the
            # *previous* mode, so switching to "Personalizat…" narrows what is
            # on screen. Reading the current one would ask the date edits for
            # their own values back and lose the preset entirely.
            start, end = self._span_for(self._mode).resolve(self._today)
            fallback = (self._today.replace(day=1), month_end(self._today))
            self._set_range(start or fallback[0], end or fallback[1])
        self._mode = mode
        self._sync_custom()
        self.changed.emit()

    def _sync_custom(self) -> None:
        custom = self._mode == CUSTOM
        self._range.setVisible(custom)
        self._calendar.setVisible(custom)
        self.adjustSize()

    def _set_range(self, start: dt.date, end: dt.date) -> None:
        for edit, value in ((self._from, start), (self._to, end)):
            edit.blockSignals(True)
            edit.setDate(QDate(value.year, value.month, value.day))
            edit.blockSignals(False)
        self._calendar.set_range(start, end)

    def _on_edited(self) -> None:
        self._calendar.set_range(to_date(self._from.date()), to_date(self._to.date()))
        self.changed.emit()

    def _on_range_picked(self, start: dt.date, end: dt.date) -> None:
        self._set_range(start, end)
        self.changed.emit()

    def _clear(self) -> None:
        self._buttons[ALL].setChecked(True)
        self._mode = ALL
        if self._delayed is not None:
            self._delayed.setChecked(False)
        self._sync_custom()
        self.changed.emit()


def popup_qss(theme: Theme) -> str:
    """The popover's own chrome: a panel card over the window, body copy inside."""
    return f"""
#filterPopup {{
    background-color: {theme.panel_bg};
    border: 1px solid {theme.border_strong};
    border-radius: 7px;
}}
#filterPopup QLabel, #filterPopup QCheckBox, #filterPopup QRadioButton {{
    color: {theme.text};
}}
#popupCaption, #popupCount {{ color: {theme.faint}; font-size: 11px; }}
#popupRule {{ color: {theme.border}; }}
#filterPopup QLineEdit, #filterPopup QDateEdit {{
    background-color: {theme.window_bg};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {RADIUS_BUTTON}px;
    padding: 4px 7px;
}}
#cifShortcut {{
    background-color: transparent;
    border: none;
    color: {theme.text};
    padding: 4px 6px;
    text-align: left;
}}
#cifShortcut:hover {{ background-color: {theme.row_hover}; border-radius: 5px; }}
#clearLink {{
    background-color: transparent;
    border: none;
    color: {theme.muted};
    font-size: 11px;
    padding: 4px 6px;
    text-decoration: underline;
}}
#clearLink:hover {{ color: {theme.accent}; }}
"""
