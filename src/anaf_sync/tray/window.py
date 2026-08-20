"""The Facturi window: toolbar, catalog table, collapsible details pane.

A resizable shell (1160×620 is the *minimum* — the size the view was designed
at) over :class:`CatalogModel`, painted by :class:`CatalogDelegate`. Setări is
a **separate window** (:mod:`settings_window`), not a page of a stack; this
window only asks for it through :attr:`MainWindow.settings_requested`, and the
same way it asks the tray to spawn a sync or a per-invoice reprocess.

**Sorting and filtering live in the table header** (:mod:`header`), not in a
row of chips above it: the label sorts, the ▽ opens that column's filter
(:mod:`filterpopups`), the boundary resizes. The one filter that has no single
column to belong to — search, which spans Număr *or* Partener — keeps its place
in the toolbar. Because a filter shut inside a popover is invisible, every
active one is echoed in the :class:`ActiveFilterBar` beneath the search field;
that band has no height at all when nothing is filtered.

The layout is elastic per DESIGN.md §10: the table absorbs extra space
(Partener is the stretch section, the other seven are user-resizable), while
the details pane and toolbar stay anchored. The pane **auto-collapses**: with
no selection it has nothing to show, so it folds to a rail and the table takes
the width back. Geometry, header layout and the pane's pinned state persist
across launches through ``QSettings`` — deliberately not ``config.toml``, which
only churns on explicit saves.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QCloseEvent, QDesktopServices, QFontMetrics, QMouseEvent
from PySide6.QtWidgets import (
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
from ..state import Archive, CatalogQuery
from . import store
from .delegates import PAD_EDGE, PAD_X, CatalogDelegate
from .details import PANE_WIDTH, DetailsPane
from .filterbar import ActiveFilterBar
from .filterpopups import (
    ChecklistFilterPopup,
    CifFilterPopup,
    DateFilterPopup,
    FilterPopup,
    TextFilterPopup,
)
from .filters import FilterState
from .format import direction_label
from .header import FUNNEL_MARGIN, MARK_BOX, MARKS_WIDTH, CatalogHeader
from .models import (
    ALL_DIRECTIONS,
    COL_DIRECTION,
    COL_FROM_CIF,
    COL_ISSUED,
    COL_NUMBER,
    COL_PARTNER,
    COL_TO_CIF,
    COL_TOTAL,
    COL_UPLOADED,
    FAILING,
    REAL_DIRECTIONS,
    CatalogModel,
)
from .theme import Theme, current_theme, window_qss

__all__ = ["MainWindow", "reveal_in_file_manager"]

_WIDTH = 1160
_HEIGHT = 620
#: What Partener must still get at the window's minimum width. It is the column
#: a reader scans, and below this a company name is an ellipsis.
_PARTNER_FLOOR = 200
#: The left column's own margins, both sides.
_MARGIN = 16

#: Column 3 (Partener) is the stretch section; these seven are fixed. The
#: mockup's px were measured in a browser at 13px, so they are a *floor*: the
#: real width also has to fit the platform's own font, both for the widest
#: value and for the header's label plus its two marks.
_COL_CONTENT = {
    COL_ISSUED: 84,
    COL_UPLOADED: 88,
    COL_NUMBER: 88,
    COL_FROM_CIF: 96,
    COL_TO_CIF: 102,
    COL_DIRECTION: 76,
    COL_TOTAL: 96,
}
_STRETCH_COL = COL_PARTNER
#: The widest value each fixed column has to hold, for that metrics check.
_COL_SAMPLES = {
    COL_ISSUED: "00.00.0000",
    COL_UPLOADED: "00.00.0000",
    COL_NUMBER: "2026-071345",
    COL_FROM_CIF: "99999999",
    COL_TO_CIF: "99999999",
    COL_DIRECTION: "trimisă",
    COL_TOTAL: "99.999,99 RON",
}
_LAST_COL = COL_TOTAL
#: Qt's header minimum is global, not per-section — one floor for all of them.
_MIN_SECTION = 72

_TITLE = "Facturi — anaf-sync"
_SETTINGS_BUTTON = "⚙  Setări…"
_SEARCH_PLACEHOLDER = "Caută după număr sau partener…"
_GEOMETRY_KEY = "facturi/geometry"
#: Versioned because the column set changed (v2 added Încărcată, v3 the two
#: role CIFs): a blob saved for a six-section header must not be replayed onto
#: eight sections. The sort indicator rides in the same blob for free.
_HEADER_KEY = "facturi/header/v3"
_COLLAPSED_KEY = "facturi/details_collapsed"

#: The header's last section carries a caret but no funnel.
MARK_SLOT = MARK_BOX + FUNNEL_MARGIN

#: How wide the folded details pane's rail is.
_RAIL_WIDTH = 30
_EXPAND_GLYPH = "‹"
_COLLAPSE_GLYPH = "›"
_EXPAND_TOOLTIP = "Arată panoul de detalii"
_NO_SELECTION_TOOLTIP = "Selectați o factură pentru detalii"
_COLLAPSE_TOOLTIP = "Restrânge panoul de detalii"

#: Which popover each filterable column opens.
_DATE_COLUMNS = frozenset({COL_ISSUED, COL_UPLOADED})
_TEXT_COLUMNS = frozenset({COL_NUMBER, COL_PARTNER})
_CIF_COLUMNS = frozenset({COL_FROM_CIF, COL_TO_CIF})
_TEXT_PLACEHOLDERS = {
    COL_NUMBER: "numărul conține…",
    COL_PARTNER: "partenerul conține…",
}


class CatalogTable(QTableView):
    """The catalog view, whose only addition is click-to-deselect.

    Re-clicking the selected row clears the selection, which folds the details
    pane. Qt's single-selection mode has no way out of a selection otherwise,
    and a pane that can only ever be closed by its own button is not one that
    "auto"-collapses.
    """

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802 — Qt override
        index = self.indexAt(e.position().toPoint())
        selection = self.selectionModel()
        if (
            index.isValid()
            and selection is not None
            and selection.isSelected(index)
            and index.row() == self.currentIndex().row()
        ):
            selection.clear()
            return
        super().mousePressEvent(e)


class MainWindow(QMainWindow):
    """The archive browser window (Facturi)."""

    #: The user asked for Setări — the tray owns that window and opens it.
    settings_requested = Signal()

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        on_retry: Callable[[], None] | None = None,
        on_reprocess: Callable[[str], None] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        super().__init__()
        self._state_path = state_path or default_state_path()
        self._on_retry = on_retry
        self._on_reprocess = on_reprocess
        self._now = now or (lambda: dt.datetime.now())  # noqa: DTZ005 — local month
        self._theme: Theme = current_theme()

        self._filters = FilterState()
        self._popup: FilterPopup | None = None
        self._details_pinned_shut = False
        self._table: CatalogTable | None = None

        self.setWindowTitle(_TITLE)
        # The design size is the minimum; the layout stretches from there
        # (DESIGN.md §10). Never a fixed size, and no maximum: a catalog can
        # always use more room — only the Setări form has a ceiling. The width
        # half is re-floored once the columns have measured themselves.
        self.setMinimumSize(_WIDTH, _HEIGHT)

        self._model = CatalogModel(self._state_path, now=self._utc_now)
        self._details = DetailsPane()
        self._details.retry_requested.connect(self._retry)
        self._details.reprocess_requested.connect(self._reprocess)
        self._details.open_pdf_requested.connect(self._open_pdf)
        self._details.reveal_requested.connect(reveal_in_file_manager)

        self._build()
        self.setMinimumWidth(self._derived_minimum_width())
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
        # Column widths and the sort indicator are UI state too, and ride in
        # their own key: the user's chosen proportions and order should survive
        # a restart like the window size does.
        header_blob = settings.value(_HEADER_KEY)
        if isinstance(header_blob, QByteArray) and self._table is not None:
            self._table.horizontalHeader().restoreState(header_blob)
            # restoreState replays interaction flags out of the blob, not just
            # geometry: the indicator Qt would draw over this header's own
            # caret, and the clickable flag that hands the sort gesture back to
            # QHeaderView. Re-assert both, so a blob written by any other build
            # cannot resurrect either. The model then has to be told the order
            # it was just handed, since a blob equal to the live one emits
            # nothing.
            self._header.setSortIndicatorShown(False)
            self._header.setSectionsClickable(False)
            self._model.sort(
                self._header.sortIndicatorSection(), self._header.sortIndicatorOrder()
            )
        self._details_pinned_shut = bool(
            settings.value(_COLLAPSED_KEY, False, type=bool)
        )
        self._sync_details_pane()

    def save_geometry_to_settings(self) -> None:
        """Persist geometry + header layout (also called by the tray on quit)."""
        settings = store.geometry_settings()
        settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        settings.setValue(_COLLAPSED_KEY, self._details_pinned_shut)
        if self._table is not None:
            settings.setValue(_HEADER_KEY, self._table.horizontalHeader().saveState())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — Qt override
        self.save_geometry_to_settings()
        super().closeEvent(event)

    def _utc_now(self) -> dt.datetime:
        return dt.datetime.now(dt.UTC)

    def _today(self) -> dt.date:
        return self._now().date()

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
        left_layout.addWidget(self._build_filter_bar())
        left_layout.addWidget(self._build_table(), 1)
        left_layout.addWidget(self._build_footer())

        layout.addWidget(left, 1)
        layout.addWidget(self._build_rail())
        layout.addWidget(self._build_details_panel())
        self.setCentralWidget(central)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        # Search stays here rather than becoming two header filters: it matches
        # Număr *or* Partener, so it has no single column to hang off.
        self._search = QLineEdit()
        self._search.setPlaceholderText(_SEARCH_PLACEHOLDER)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search, 1)

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

    def _build_filter_bar(self) -> QWidget:
        self._filter_bar = ActiveFilterBar()
        self._filter_bar.chip_removed.connect(self._remove_filter)
        self._filter_bar.cleared.connect(self._clear_filters)
        return self._filter_bar

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

    def _build_details_panel(self) -> QWidget:
        """The pane plus its own ``›``, so the control hides with what it closes."""
        panel = QWidget()
        panel.setObjectName("detailsPanel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setFixedWidth(PANE_WIDTH)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        bar = QWidget()
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(0, 8, 8, 0)
        bar_layout.addStretch(1)
        self._collapse_button = QToolButton()
        self._collapse_button.setObjectName("railButton")
        self._collapse_button.setText(_COLLAPSE_GLYPH)
        self._collapse_button.setToolTip(_COLLAPSE_TOOLTIP)
        self._collapse_button.clicked.connect(self._collapse_details)
        bar_layout.addWidget(self._collapse_button)

        layout.addWidget(bar)
        layout.addWidget(self._details, 1)
        self._details_panel = panel
        return panel

    def _build_rail(self) -> QWidget:
        """The folded details pane: one chevron, and nothing else to misread."""
        self._rail = QWidget()
        self._rail.setObjectName("detailsRail")
        self._rail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._rail.setFixedWidth(_RAIL_WIDTH)
        layout = QVBoxLayout(self._rail)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(0)
        self._expand_button = QToolButton()
        self._expand_button.setObjectName("railButton")
        self._expand_button.setText(_EXPAND_GLYPH)
        self._expand_button.clicked.connect(self._open_details)
        layout.addWidget(self._expand_button)
        layout.addStretch(1)
        return self._rail

    def _build_table(self) -> CatalogTable:
        table = CatalogTable()
        table.setModel(self._model)
        self._delegate = CatalogDelegate(table)
        table.setItemDelegate(self._delegate)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        table.setMouseTracking(True)

        # Not setSortingEnabled: that wires every section click straight to
        # model.sort, including Direcție's, which must not sort. The header
        # decides, then reports through Qt's own sortIndicatorChanged.
        self._header = CatalogHeader(table)
        table.setHorizontalHeader(self._header)
        self._header.setMinimumSectionSize(_MIN_SECTION)
        self._header.setSortIndicator(self._model.sort_column, self._model.sort_order)
        self._header.sortIndicatorChanged.connect(self._model.sort)
        self._header.filter_requested.connect(self._open_filter)
        # Partener stretches; the other seven are Interactive, so dragging a
        # header boundary re-proportions exactly one of them and Partener
        # absorbs the difference — the table can never exceed its viewport.
        self._header.setSectionResizeMode(_STRETCH_COL, QHeaderView.ResizeMode.Stretch)
        metrics = table.fontMetrics()
        for col in _COL_CONTENT:
            self._header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(col, _section_width(metrics, col, self._label(col)))

        selection = table.selectionModel()
        if selection is not None:
            selection.currentRowChanged.connect(self._on_row_changed)
            selection.selectionChanged.connect(lambda *_: self._on_selection_changed())
        self._model.modelReset.connect(self._update_footer)
        self._model.rowsInserted.connect(lambda *_: self._update_footer())
        self._table = table
        return table

    def _derived_minimum_width(self) -> int:
        """The design minimum, floored by what the columns actually need.

        Each fixed section sizes itself from the platform's own font — its
        label plus the header's two marks — which on most desktops is wider
        than the px the mockup was measured at. A constant minimum would
        therefore squeeze Partener hardest on exactly the machines whose
        metrics are widest, so the floor is *derived* instead, the way Setări
        derives its own from the variable reference panel (DESIGN.md §10).
        """
        if self._table is None:
            return _WIDTH
        sections = sum(self._table.columnWidth(col) for col in _COL_CONTENT)
        chrome = _MARGIN * 2 + PANE_WIDTH + 1  # margins + pane + its border
        return max(_WIDTH, sections + _PARTNER_FLOOR + chrome)

    def _label(self, col: int) -> str:
        return str(self._model.headerData(col, self._header.orientation()))

    # -- filters --------------------------------------------------------------

    def _on_search(self, text: str) -> None:
        self._filters = dataclasses.replace(self._filters, search=text.strip())
        self._apply_filters()

    def _remove_filter(self, key: str) -> None:
        self._filters = self._filters.without(key)
        self._apply_filters()

    def _clear_filters(self) -> None:
        self._filters = self._filters.cleared()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Re-query, then repaint the three views that read the filter state."""
        selected = self._selected_id()
        today = self._today()
        self._model.set_filters(self._filters.to_filters(today))
        self._header.set_filtered(self._filters.active_columns())
        self._filter_bar.set_chips(self._filters.chips(today))
        # The selection survives unless the row it names has been filtered out
        # — clearing it on every change would throw away a pane mid-read, and
        # with an auto-collapsing pane it would make the whole right side flap.
        self._reselect(selected)
        self._update_footer()

    def _open_filter(self, section: int) -> None:
        """Open one column's popover, seeded from the current filter state."""
        popup = self._build_popup(section)
        if popup is None:
            return
        popup.changed.connect(lambda: self._read_popup(section, popup))
        # Held on the window, not just on the stack: a Qt.Popup with no
        # reference is garbage-collected the moment this method returns.
        self._popup = popup
        popup.open_under(self._section_rect(section))

    def _build_popup(self, section: int) -> FilterPopup | None:
        theme = self._theme
        if section in _DATE_COLUMNS:
            uploaded = section == COL_UPLOADED
            return DateFilterPopup(
                span=self._filters.uploaded if uploaded else self._filters.issued,
                today=self._today(),
                with_delayed=uploaded,
                delayed=self._filters.delayed_only,
                theme=theme,
            )
        if section in _TEXT_COLUMNS:
            value = (
                self._filters.number if section == COL_NUMBER else self._filters.partner
            )
            return TextFilterPopup(
                value=value, placeholder=_TEXT_PLACEHOLDERS[section], theme=theme
            )
        if section in _CIF_COLUMNS:
            cif = (
                self._filters.from_cif
                if section == COL_FROM_CIF
                else self._filters.to_cif
            )
            return CifFilterPopup(
                value=cif, own_cifs=self._own_cif_counts(section), theme=theme
            )
        if section == COL_DIRECTION:
            return ChecklistFilterPopup(
                options=self._direction_options(),
                checked=self._filters.directions,
                theme=theme,
            )
        return None

    def _read_popup(self, section: int, popup: FilterPopup) -> None:
        """Copy the popover's live value back into the filter state."""
        if isinstance(popup, DateFilterPopup):
            if section == COL_UPLOADED:
                self._filters = dataclasses.replace(
                    self._filters, uploaded=popup.span, delayed_only=popup.delayed
                )
            else:
                self._filters = dataclasses.replace(self._filters, issued=popup.span)
        elif isinstance(popup, TextFilterPopup):
            self._filters = (
                dataclasses.replace(self._filters, number=popup.value)
                if section == COL_NUMBER
                else dataclasses.replace(self._filters, partner=popup.value)
            )
        elif isinstance(popup, CifFilterPopup):
            self._filters = (
                dataclasses.replace(self._filters, from_cif=popup.value)
                if section == COL_FROM_CIF
                else dataclasses.replace(self._filters, to_cif=popup.value)
            )
        elif isinstance(popup, ChecklistFilterPopup):
            self._filters = dataclasses.replace(self._filters, directions=popup.checked)
        self._apply_filters()

    def _section_rect(self, section: int) -> QRect:
        """The header section's rectangle in global coordinates."""
        viewport = self._header.viewport()
        left = self._header.sectionViewportPosition(section)
        top_left = viewport.mapToGlobal(QPoint(left, 0))
        return QRect(
            top_left, QSize(self._header.sectionSize(section), self._header.height())
        )

    def _own_cif_counts(self, section: int) -> Sequence[tuple[str, int]]:
        """The followed CIFs and how many rows each would match in this column.

        Counted with the same ``LIKE`` the filter itself uses, so the number
        beside a shortcut is exactly what clicking it yields.
        """
        if not self._state_path.exists():
            return ()
        with Archive.open_readonly(self._state_path) as archive:
            if section == COL_FROM_CIF:
                return tuple(
                    (cif, archive.catalog_count(CatalogQuery(from_cif=cif)))
                    for cif in archive.distinct_cifs()
                )
            return tuple(
                (cif, archive.catalog_count(CatalogQuery(to_cif=cif)))
                for cif in archive.distinct_cifs()
            )

    def _direction_options(self) -> Sequence[tuple[str, str, int]]:
        """``(value, label, count)`` for the Direcție checklist.

        The failing count comes from the other table entirely — that is what
        makes "eșuată" a value of this filter and not of the direction column.
        """
        if not self._state_path.exists():
            return ()
        with Archive.open_readonly(self._state_path) as archive:
            counts = {
                value: archive.catalog_count(
                    CatalogQuery(directions=frozenset({value}))
                )
                for value in REAL_DIRECTIONS
            }
            counts[FAILING] = len(archive.failures)
        return tuple(
            (value, direction_label(value), counts[value])
            for value in sorted(ALL_DIRECTIONS)
        )

    def _update_footer(self) -> None:
        shown, total = self._model.shown_count(), self._model.total_count()
        self._footer.setText(f"{shown} afișate · {total} în arhivă")

    # -- selection + the collapsing pane --------------------------------------

    def _selected_id(self) -> str | None:
        if self._table is None:
            return None
        selection = self._table.selectionModel()
        if selection is None or not selection.hasSelection():
            return None
        return self._message_id_at(selection.currentIndex())

    def _reselect(self, message_id: str | None) -> None:
        if self._table is None:
            return
        selection = self._table.selectionModel()
        if selection is None:
            return
        row = self._model.row_of(message_id) if message_id is not None else None
        if row is None:
            selection.clear()
            self._details.show_record(None)
        else:
            self._table.selectRow(row)
        self._sync_details_pane()

    def _on_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            self._details.show_record(None)
        else:
            self._details.show_record(self._model.entry(current.row()))

    def _on_selection_changed(self) -> None:
        """A row was chosen or dropped — the pane follows without being asked.

        Choosing a row deliberately does *not* un-pin a folded pane. Folding it
        is a preference about the layout, not a remark about one invoice; if the
        next selection reopened it, the ``›`` would be a one-row undo and the
        state saved across launches would never once take effect.
        """
        if self._selected_id() is None:
            self._details.show_record(None)
        self._sync_details_pane()

    def _open_details(self) -> None:
        self._details_pinned_shut = False
        self._sync_details_pane()

    def _collapse_details(self) -> None:
        self._details_pinned_shut = True
        self._sync_details_pane()

    def _sync_details_pane(self) -> None:
        """Show the pane only when it has something to show and is not pinned."""
        has_selection = self._selected_id() is not None
        open_pane = has_selection and not self._details_pinned_shut
        self._details_panel.setVisible(open_pane)
        self._rail.setVisible(not open_pane)
        self._expand_button.setEnabled(has_selection)
        self._expand_button.setToolTip(
            _EXPAND_TOOLTIP if has_selection else _NO_SELECTION_TOOLTIP
        )

    # -- actions --------------------------------------------------------------

    def _open_pdf(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _retry(self) -> None:
        if self._on_retry is not None:
            self._on_retry()

    def _reprocess(self, message_id: str) -> None:
        if self._on_reprocess is not None:
            self._on_reprocess(message_id)

    def set_busy(self, busy: bool) -> None:
        """Pass a tray-spawned command's in-flight state to the details pane."""
        self._details.set_busy(busy)

    # -- theme ----------------------------------------------------------------

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(_window_stylesheet(theme))
        self._delegate.set_theme(theme)
        self._details.set_theme(theme)
        self._header.set_theme(theme)
        self._filter_bar.set_theme(theme)
        if self._table is not None:
            self._table.viewport().update()

    def refresh(self) -> None:
        """Re-read the archive (called when the tray detects a state change).

        Anchored: the top visible row and the selected row are re-found by
        message id after the reset, so a refresh mid-read never teleports the
        catalog back to the top or drops the details pane.
        """
        if self._table is None:
            self._model.reload()
            self._update_footer()
            return
        top_id = self._message_id_at(self._table.indexAt(QPoint(0, 0)))
        selected_id = self._selected_id()
        self._model.reload()
        # Selection first: re-selecting auto-scrolls to the row, so the top
        # anchor must win by coming last.
        self._reselect(selected_id)
        if top_id is not None and (row := self._model.row_of(top_id)) is not None:
            self._table.scrollTo(
                self._model.index(row, 0), QTableView.ScrollHint.PositionAtTop
            )
        self._update_footer()

    def _message_id_at(self, index: QModelIndex) -> str | None:
        if not index.isValid():
            return None
        message_id = self._model.data(index, CatalogModel.MessageIdRole)
        return message_id if isinstance(message_id, str) else None


def _section_width(metrics: QFontMetrics, col: int, label: str) -> int:
    """What a fixed section must be wide enough for, plus the delegate's padding.

    Three floors, not one: the mockup's measured width, the widest value in the
    platform's own font, and — since the header now carries a sort caret and a
    funnel — the uppercased label plus both marks. Missing the third clips a
    header label under its own controls.
    """
    content = max(_COL_CONTENT[col], metrics.horizontalAdvance(_COL_SAMPLES[col]))
    header = metrics.horizontalAdvance(label.upper()) + MARKS_WIDTH
    left = PAD_EDGE if col == 0 else PAD_X
    right = PAD_EDGE if col == _LAST_COL else PAD_X
    return max(content, header) + left + right


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
#settingsButton {{ border-radius:6px; }}
#settingsButton:hover {{ background-color:{theme.row_hover};
    color:{theme.text}; }}
#detailsRail, #detailsPanel {{ background-color:{theme.window_bg};
    border-left:1px solid {theme.border}; }}
#railButton {{ background-color:transparent; border:none; color:{theme.muted};
    font-size:15px; padding:2px; }}
#railButton:hover {{ color:{theme.accent}; }}
#railButton:disabled {{ color:{theme.faint}; }}
QLineEdit {{ background-color:{theme.window_bg}; color:{theme.text};
    border:1px solid {theme.border}; border-radius:6px; padding:5px 8px; }}
QTableView {{ background-color:{theme.panel_bg}; color:{theme.text};
    border:1px solid {theme.border}; gridline-color:{theme.border};
    selection-background-color:{theme.row_selected};
    selection-color:{theme.text}; }}
/* Padding mirrors the delegate's so headers sit over their own columns, and
   reserves the right edge for the caret and funnel the header paints there. */
QHeaderView::section {{ background-color:{theme.panel_bg}; color:{theme.faint};
    border:none; border-bottom:1px solid {theme.border};
    border-right:1px solid {theme.border};
    padding:6px {MARKS_WIDTH}px 6px 4px; text-transform:uppercase; }}
QHeaderView::section:hover {{ background-color:{theme.row_hover};
    color:{theme.text}; }}
QHeaderView::section:first {{ padding-left:14px; }}
/* Total has no funnel, so its label only has to clear the sort caret — and
   then lines up with the values beneath it. */
QHeaderView::section:last {{ border-right:none;
    padding-right:{MARK_SLOT}px; }}
"""
