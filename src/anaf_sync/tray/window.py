"""The Facturi main window: sidebar, toolbar, catalog table, details pane.

Fixed 980×620 shell with a Facturi view over :class:`CatalogModel` (painted by
:class:`CatalogDelegate`) and a placeholder Setări page (filled in M3). The
window is a pure observer: selecting a row swaps the details pane; its buttons
emit intents the window turns into file-manager / sync actions. Filters
(direction chips ∧ period ∧ search) combine into one :class:`CatalogFilters`.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDate, QModelIndex, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import default_config_path, default_state_path
from . import strings
from .calendar import RangeCalendar
from .delegates import CatalogDelegate
from .details import DetailsPane
from .models import CatalogFilters, CatalogModel
from .settings_view import SettingsView
from .theme import Theme, current_theme, window_qss

__all__ = ["MainWindow", "reveal_in_file_manager"]

_WIDTH = 980
_HEIGHT = 620
_COL_WIDTHS = {0: 52, 1: 88, 3: 76, 4: 96}  # column 2 (Partener) flexes


class MainWindow(QMainWindow):
    """The archive browser window (Facturi + Setări)."""

    #: Emitted after Setări writes ``config.toml`` (the tray refreshes on it).
    config_saved = Signal()

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        config_path: Path | None = None,
        on_retry: Callable[[], None] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        super().__init__()
        self._state_path = state_path or default_state_path()
        self._config_path = config_path or default_config_path()
        self._on_retry = on_retry
        self._now = now or (lambda: dt.datetime.now())  # noqa: DTZ005 — local month
        self._theme: Theme = current_theme()

        self._direction: str | None = None
        self._problems = False
        self._period_from: dt.date | None = None
        self._period_to: dt.date | None = None
        self._table: QTableView | None = None

        self.setWindowTitle(strings.WINDOW_TITLE)
        self.setFixedSize(_WIDTH, _HEIGHT)

        self._model = CatalogModel(self._state_path, now=self._utc_now)
        self._details = DetailsPane()
        self._details.retry_requested.connect(self._retry)
        self._details.open_pdf_requested.connect(self._open_pdf)
        self._details.reveal_requested.connect(reveal_in_file_manager)

        self._build()
        self.apply_theme(self._theme)
        self._apply_filters()

    def _utc_now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_titlebar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_facturi())
        self._stack.addWidget(self._build_settings())
        body_layout.addWidget(self._stack, 1)

        outer.addWidget(body, 1)
        self.setCentralWidget(central)

    def _build_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("titlebar")
        bar.setFixedHeight(38)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        self._title_label = QLabel(strings.WINDOW_TITLE)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._title_label)
        return bar

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(148)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._nav_group = QButtonGroup(self)
        self._nav_invoices = self._nav_button(strings.SIDEBAR_INVOICES, 0)
        self._nav_settings = self._nav_button(strings.SIDEBAR_SETTINGS, 1)
        self._nav_invoices.setChecked(True)
        layout.addWidget(self._nav_invoices)
        layout.addWidget(self._nav_settings)
        return sidebar

    def _nav_button(self, text: str, page: int) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.clicked.connect(lambda: self._stack.setCurrentIndex(page))
        self._nav_group.addButton(button)
        return button

    def _build_facturi(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 12, 16, 12)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._build_toolbar())
        left_layout.addWidget(self._build_period_row())
        left_layout.addWidget(self._build_table(), 1)
        self._footer = QLabel()
        self._footer.setObjectName("footer")
        left_layout.addWidget(self._footer)
        layout.addWidget(left, 1)
        layout.addWidget(self._details)
        return page

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText(strings.SEARCH_PLACEHOLDER)
        self._search.textChanged.connect(self._apply_filters)
        layout.addWidget(self._search, 1)

        self._filter_group = QButtonGroup(self)
        self._chip_all = self._chip(strings.FILTER_ALL)
        self._chip_received = self._chip(strings.FILTER_RECEIVED)
        self._chip_sent = self._chip(strings.FILTER_SENT)
        self._chip_problems = self._chip(strings.problems_chip(0))
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
        return bar

    def _build_period_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel(strings.PERIOD_LABEL))

        self._period_group = QButtonGroup(self)
        self._period_month = self._chip(strings.PERIOD_CURRENT)
        self._period_all = self._chip(strings.PERIOD_ALL)
        self._period_custom = self._chip(strings.PERIOD_CUSTOM)
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
            edit.setFixedWidth(112)
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
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col, width in _COL_WIDTHS.items():
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(col, width)
        table.selectionModel().currentRowChanged.connect(self._on_row_changed)
        self._model.modelReset.connect(self._update_footer)
        self._model.rowsInserted.connect(lambda *_: self._update_footer())
        self._table = table
        return table

    def _build_settings(self) -> QWidget:
        self._settings = SettingsView(
            state_path=self._state_path, config_path=self._config_path
        )
        self._settings.saved.connect(self._on_config_saved)
        return self._settings

    def _on_config_saved(self) -> None:
        self.refresh()
        self.config_saved.emit()

    def show_settings(self) -> None:
        """Switch to the Setări page (used by the tray's Setări… item)."""
        self._nav_settings.setChecked(True)
        self._stack.setCurrentIndex(1)

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
        self._period_from = _qdate_to_date(self._date_from.date())
        self._period_to = _qdate_to_date(self._date_to.date())
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
        self._footer.setText(
            strings.footer_text(self._model.shown_count(), self._model.total_count())
        )
        self._chip_problems.setText(strings.problems_chip(self._model.problem_count()))

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
        self._settings.set_theme(theme)
        if self._table is not None:
            self._table.viewport().update()

    def refresh(self) -> None:
        """Re-read the archive (called when the tray detects a state change)."""
        self._model.reload()
        self._update_footer()


def _qdate_to_date(qdate: QDate) -> dt.date:
    return dt.date(qdate.year(), qdate.month(), qdate.day())


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
#titlebar {{ background-color:{theme.panel_bg};
    border-bottom:1px solid {theme.border}; }}
#titlebar QLabel {{ color:{theme.muted}; }}
#sidebar {{ background-color:{theme.window_bg};
    border-right:1px solid {theme.border}; }}
#footer {{ color:{theme.faint}; font-size:11px; }}
QToolButton {{ background-color:{theme.window_bg}; color:{theme.muted};
    border:1px solid {theme.border}; border-radius:9px; padding:4px 10px; }}
QToolButton:checked {{ background-color:{theme.accent}; color:{theme.on_accent};
    border-color:{theme.accent}; }}
QLineEdit {{ background-color:{theme.window_bg}; color:{theme.text};
    border:1px solid {theme.border}; border-radius:6px; padding:5px 8px; }}
QTableView {{ background-color:{theme.panel_bg}; color:{theme.text};
    border:1px solid {theme.border}; gridline-color:{theme.border};
    selection-background-color:{theme.row_selected};
    selection-color:{theme.text}; }}
QHeaderView::section {{ background-color:{theme.panel_bg}; color:{theme.faint};
    border:none; border-bottom:1px solid {theme.border};
    padding:6px 14px; text-transform:uppercase; }}
"""
