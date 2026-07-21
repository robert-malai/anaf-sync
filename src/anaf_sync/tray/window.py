"""The Facturi window: toolbar, period row, catalog table, details pane.

A resizable shell (980×620 is the *minimum* — the size the view was designed
at) over :class:`CatalogModel`, painted by :class:`CatalogDelegate`. Setări is
a **separate window** (:mod:`settings_window`), not a page of a stack; this
window only asks for it through :attr:`MainWindow.settings_requested`. The
layout is elastic per DESIGN.md §10: the table absorbs extra space (Partener
is the stretch section, the other four are user-resizable), while the details
pane and toolbar rows stay anchored. Geometry and header layout persist
across launches through ``QSettings`` — deliberately not ``config.toml``,
which only churns on explicit saves. The window is a pure observer: selecting
a row swaps the details pane; its buttons emit intents the window turns into
file-manager / sync actions. Filters (direction chips ∧ period ∧ search)
combine into one :class:`CatalogFilters`.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QDate, QModelIndex, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFontMetrics
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import default_state_path
from . import store
from .calendar import RangeCalendar, to_date
from .delegates import PAD_EDGE, PAD_X, CatalogDelegate
from .details import DetailsPane
from .models import CatalogFilters, CatalogModel
from .theme import Theme, current_theme, window_qss

__all__ = ["MainWindow", "reveal_in_file_manager"]

_WIDTH = 980
_HEIGHT = 620

#: Column 2 (Partener) is the stretch section; these four are fixed. The
#: mockup's px were measured in a browser at 13px, so they are a *floor*: the
#: real width also has to fit the platform's own font, or a date or a total
#: would render clipped on a machine with wider metrics.
_COL_CONTENT = {0: 84, 1: 88, 3: 76, 4: 96}
#: The widest value each fixed column has to hold, for that metrics check.
_COL_SAMPLES = {0: "00.00.0000", 1: "2026-071345", 3: "trimisă", 4: "99.999,99 RON"}
_LAST_COL = 4
#: Qt's header minimum is global, not per-section — one floor for all of them.
_MIN_SECTION = 72

_TITLE = "Facturi — anaf-sync"
_SETTINGS_BUTTON = "⚙  Setări…"
_GEOMETRY_KEY = "facturi/geometry"
_HEADER_KEY = "facturi/header"


def _problems_chip_text(count: int) -> str:
    """``"Probleme"`` / ``"Probleme (1)"`` — suffix the count when non-zero."""
    return "Probleme" if count == 0 else f"Probleme ({count})"


class MainWindow(QMainWindow):
    """The archive browser window (Facturi)."""

    #: The user asked for Setări — the tray owns that window and opens it.
    settings_requested = Signal()

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        on_retry: Callable[[], None] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        super().__init__()
        self._state_path = state_path or default_state_path()
        self._on_retry = on_retry
        self._now = now or (lambda: dt.datetime.now())  # noqa: DTZ005 — local month
        self._theme: Theme = current_theme()

        self._direction: str | None = None
        self._problems = False
        self._period_from: dt.date | None = None
        self._period_to: dt.date | None = None
        self._table: QTableView | None = None

        self.setWindowTitle(_TITLE)
        # The design size is the minimum; the layout stretches from there
        # (DESIGN.md §10). Never a fixed size, and no maximum: a catalog can
        # always use more room — only the Setări form has a ceiling.
        self.setMinimumSize(_WIDTH, _HEIGHT)

        self._model = CatalogModel(self._state_path, now=self._utc_now)
        self._details = DetailsPane()
        self._details.retry_requested.connect(self._retry)
        self._details.open_pdf_requested.connect(self._open_pdf)
        self._details.reveal_requested.connect(reveal_in_file_manager)

        self._build()
        self.apply_theme(self._theme)
        self._apply_filters()
        self._restore_geometry()

    # -- geometry persistence ---------------------------------------------------

    def _restore_geometry(self) -> None:
        # restoreGeometry also recovers maximised state and pulls a position
        # remembered on a detached monitor back onto a live screen; a missing
        # or invalid blob leaves the design-size default.
        settings = store.geometry_settings()
        blob = settings.value(_GEOMETRY_KEY)
        if isinstance(blob, QByteArray):
            self.restoreGeometry(blob)
        # Column widths are UI state too, and ride in their own key: the user's
        # chosen proportions should survive a restart like the window size does.
        header_blob = settings.value(_HEADER_KEY)
        if isinstance(header_blob, QByteArray) and self._table is not None:
            self._table.horizontalHeader().restoreState(header_blob)

    def save_geometry_to_settings(self) -> None:
        """Persist geometry + header layout (also called by the tray on quit)."""
        settings = store.geometry_settings()
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        if self._table is not None:
            settings.setValue(_HEADER_KEY, self._table.horizontalHeader().saveState())

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_geometry_to_settings()
        super().closeEvent(event)

    def _utc_now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 12, 16, 12)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._build_toolbar())
        left_layout.addWidget(self._build_period_row())
        left_layout.addWidget(self._build_table(), 1)
        left_layout.addWidget(self._build_footer())

        layout.addWidget(left, 1)
        layout.addWidget(self._details)
        self.setCentralWidget(central)

    def _build_footer(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        self._footer = QLabel()
        self._footer.setObjectName("footer")
        hint = QLabel("lista se încarcă pe măsură ce derulați")
        hint.setObjectName("footer")
        layout.addWidget(self._footer)
        layout.addStretch(1)
        layout.addWidget(hint)
        return row

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Caută după număr sau partener…")
        self._search.textChanged.connect(self._apply_filters)
        layout.addWidget(self._search, 1)

        self._filter_group = QButtonGroup(self)
        self._chip_all = self._chip("Toate")
        self._chip_received = self._chip("Primite")
        self._chip_sent = self._chip("Trimise")
        self._chip_problems = self._chip(_problems_chip_text(0))
        self._chip_all.setChecked(True)
        self._chip_all.clicked.connect(lambda: self._set_direction(None, False))
        self._chip_received.clicked.connect(
            lambda: self._set_direction("received", False)
        )
        self._chip_sent.clicked.connect(lambda: self._set_direction("sent", False))
        self._chip_problems.clicked.connect(lambda: self._set_direction(None, True))
        for chip in (
            self._chip_all,
            self._chip_received,
            self._chip_sent,
            self._chip_problems,
        ):
            self._filter_group.addButton(chip)
            layout.addWidget(chip)

        separator = QFrame()
        separator.setObjectName("toolbarSeparator")
        separator.setFrameShape(QFrame.Shape.VLine)
        layout.addWidget(separator)

        self._settings_button = QToolButton()
        self._settings_button.setObjectName("settingsButton")
        self._settings_button.setText(_SETTINGS_BUTTON)
        self._settings_button.setToolTip("Deschide fereastra Setări")
        self._settings_button.clicked.connect(self.settings_requested)
        layout.addWidget(self._settings_button)
        return bar

    def _build_period_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel("Perioadă"))

        self._period_group = QButtonGroup(self)
        self._period_month = self._chip("Luna curentă")
        self._period_all = self._chip("Toate")
        self._period_custom = self._chip("Personalizat…")
        self._period_all.setChecked(True)
        self._period_month.clicked.connect(self._period_current_month)
        self._period_all.clicked.connect(self._period_all_time)
        self._period_custom.clicked.connect(self._period_custom_mode)
        for chip in (self._period_month, self._period_all, self._period_custom):
            self._period_group.addButton(chip)
            layout.addWidget(chip)

        self._date_from = QDateEdit()
        self._date_to = QDateEdit()
        for edit in (self._date_from, self._date_to):
            edit.setDisplayFormat("dd.MM.yyyy")
            edit.setFixedWidth(96)
            edit.setVisible(False)
            edit.dateChanged.connect(self._on_date_edit)
        layout.addWidget(self._date_from)
        layout.addWidget(self._date_to)

        self._calendar = RangeCalendar()
        self._calendar.setVisible(False)
        self._calendar.range_selected.connect(self._on_range_selected)

        layout.addStretch(1)
        return row

    def _build_table(self) -> QTableView:
        table = QTableView()
        table.setModel(self._model)
        self._delegate = CatalogDelegate(table)
        table.setItemDelegate(self._delegate)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        table.setMouseTracking(True)
        header = table.horizontalHeader()
        header.setMinimumSectionSize(_MIN_SECTION)
        # Partener stretches; the other four are Interactive, so dragging a
        # header boundary re-proportions exactly one of them and Partener
        # absorbs the difference — the table can never exceed its viewport.
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        metrics = table.fontMetrics()
        for col in _COL_CONTENT:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(col, _section_width(metrics, col))
        table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._model.modelReset.connect(self._update_footer)
        self._model.rowsInserted.connect(lambda *_: self._update_footer())
        self._table = table
        return table

    def _chip(self, text: str) -> QToolButton:
        chip = QToolButton()
        chip.setText(text)
        chip.setCheckable(True)
        return chip

    # -- filters --------------------------------------------------------------

    def _set_direction(self, direction: str | None, problems: bool) -> None:
        self._direction = direction
        self._problems = problems
        self._apply_filters()

    def _period_current_month(self) -> None:
        today = self._now().date()
        self._period_from = today.replace(day=1)
        self._period_to = _month_end(today)
        self._set_custom_visible(False)
        self._apply_filters()

    def _period_all_time(self) -> None:
        self._period_from = self._period_to = None
        self._set_custom_visible(False)
        self._apply_filters()

    def _period_custom_mode(self) -> None:
        self._set_custom_visible(True)
        self._on_date_edit()

    def _set_custom_visible(self, visible: bool) -> None:
        self._date_from.setVisible(visible)
        self._date_to.setVisible(visible)
        self._calendar.setVisible(visible)

    def _on_date_edit(self) -> None:
        if not self._date_from.isVisible():
            return
        self._period_from = to_date(self._date_from.date())
        self._period_to = to_date(self._date_to.date())
        self._calendar.set_range(self._period_from, self._period_to)
        self._apply_filters()

    def _on_range_selected(self, start: dt.date, end: dt.date) -> None:
        self._date_from.setDate(QDate(start.year, start.month, start.day))
        self._date_to.setDate(QDate(end.year, end.month, end.day))

    def _apply_filters(self) -> None:
        self._model.set_filters(
            CatalogFilters(
                search=self._search.text().strip() or None,
                direction=self._direction,
                issued_from=self._period_from,
                issued_to=self._period_to,
                problems_only=self._problems,
            )
        )
        self._details.show_record(None)
        self._update_footer()

    def _update_footer(self) -> None:
        shown, total = self._model.shown_count(), self._model.total_count()
        self._footer.setText(f"{shown} afișate · {total} în arhivă")
        self._chip_problems.setText(_problems_chip_text(self._model.problem_count()))

    # -- selection + actions --------------------------------------------------

    def _on_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            self._details.show_record(None)
            return
        self._details.show_record(self._model.entry(current.row()))

    def _open_pdf(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _retry(self) -> None:
        if self._on_retry is not None:
            self._on_retry()

    # -- theme ----------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(_window_stylesheet(theme))
        self._delegate.set_theme(theme)
        self._details.set_theme(theme)
        self._calendar.set_theme(theme)
        if self._table is not None:
            self._table.viewport().update()

    def refresh(self) -> None:
        """Re-read the archive (called when the tray detects a state change)."""
        self._model.reload()
        self._update_footer()


def _section_width(metrics: QFontMetrics, col: int) -> int:
    """The mockup's content width, floored by what the real font needs, plus
    the padding the delegate insets on that column."""
    content = max(_COL_CONTENT[col], metrics.horizontalAdvance(_COL_SAMPLES[col]))
    left = PAD_EDGE if col == 0 else PAD_X
    right = PAD_EDGE if col == _LAST_COL else PAD_X
    return content + left + right


def _month_end(day: dt.date) -> dt.date:
    if day.month == 12:
        return day.replace(day=31)
    return day.replace(month=day.month + 1, day=1) - dt.timedelta(days=1)


def reveal_in_file_manager(path: Path) -> None:
    """Select ``path`` in the OS file manager (platform-dispatched)."""
    target = Path(path)
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(target)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{target}"], check=False)
    else:
        directory = target if target.is_dir() else target.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))


def _window_stylesheet(theme: Theme) -> str:
    return window_qss(theme) + f"""
#footer {{ color:{theme.faint}; font-size:11px; }}
#toolbarSeparator {{ color:{theme.border}; }}
QToolButton {{ background-color:{theme.window_bg}; color:{theme.muted};
    border:1px solid {theme.border}; border-radius:9px; padding:4px 10px; }}
QToolButton:checked {{ background-color:{theme.accent}; color:{theme.on_accent};
    border-color:{theme.accent}; }}
/* Not a filter chip: it never latches, so it must not read as "off". */
#settingsButton {{ border-radius:6px; }}
#settingsButton:hover {{ background-color:{theme.row_hover};
    color:{theme.text}; }}
QLineEdit {{ background-color:{theme.window_bg}; color:{theme.text};
    border:1px solid {theme.border}; border-radius:6px; padding:5px 8px; }}
QTableView {{ background-color:{theme.panel_bg}; color:{theme.text};
    border:1px solid {theme.border}; gridline-color:{theme.border};
    selection-background-color:{theme.row_selected};
    selection-color:{theme.text}; }}
/* Padding mirrors the delegate's so headers sit over their own columns. */
QHeaderView::section {{ background-color:{theme.panel_bg}; color:{theme.faint};
    border:none; border-bottom:1px solid {theme.border};
    border-right:1px solid {theme.border};
    padding:6px 4px; text-transform:uppercase; }}
QHeaderView::section:first {{ padding-left:14px; }}
QHeaderView::section:last {{ padding-right:14px; border-right:none; }}
"""
