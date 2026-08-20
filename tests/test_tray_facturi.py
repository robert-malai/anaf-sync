"""Qt-dependent Facturi pieces: model, calendar, delegate, details, window."""

import dataclasses
import datetime as dt
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QHeaderView,
    QStackedWidget,
)
from sample_data import OWN_CIFS, seed_sample_archive  # noqa: E402

from anaf_sync.state import Archive, CatalogEntry  # noqa: E402
from anaf_sync.tray.calendar import RangeCalendar  # noqa: E402
from anaf_sync.tray.details import artifact_path  # noqa: E402
from anaf_sync.tray.filterpopups import (  # noqa: E402
    ChecklistFilterPopup,
    CifFilterPopup,
    DateFilterPopup,
    TextFilterPopup,
)
from anaf_sync.tray.filters import KEY_PARTNER, FilterState  # noqa: E402
from anaf_sync.tray.header import FUNNEL_MARGIN  # noqa: E402
from anaf_sync.tray.models import (  # noqa: E402
    COL_DIRECTION,
    COL_FROM_CIF,
    COL_ISSUED,
    COL_NUMBER,
    COL_PARTNER,
    COL_TO_CIF,
    COL_TOTAL,
    COL_UPLOADED,
    FAILING,
    CatalogFilters,
    CatalogModel,
)
from anaf_sync.tray.period import ALL, CUSTOM, THIS_MONTH, DateSpan  # noqa: E402
from anaf_sync.tray.window import MARK_SLOT, MainWindow  # noqa: E402

_NOW = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.UTC)


def _model(tmp_path: Path) -> CatalogModel:
    seed_sample_archive(tmp_path / "state.db")
    return CatalogModel(tmp_path / "state.db", now=lambda: _NOW)


def _ids(model: CatalogModel) -> list[str]:
    return [
        model.data(model.index(r, 0), CatalogModel.MessageIdRole)
        for r in range(model.rowCount())
    ]


# -- model: ordering, pinning, roles -----------------------------------------


def test_failing_row_pinned_on_top(tmp_path: Path) -> None:
    model = _model(tmp_path)
    assert model.rowCount() == 6  # 5 catalog + 1 failing
    top = model.index(0, 0)
    assert model.data(top, CatalogModel.FailingRole) is True
    assert model.data(top, CatalogModel.MessageIdRole) == "3210447810"


def test_catalog_ordered_newest_first_after_pin(tmp_path: Path) -> None:
    model = _model(tmp_path)
    # failing, then issue_date desc: 18, 17, 15, 11, 3.
    assert _ids(model) == [
        "3210447810",
        "3210447811",
        "3210447812",
        "3210447813",
        "3210447814",
        "3210447815",
    ]


def test_delayed_role_flags_ff88214(tmp_path: Path) -> None:
    model = _model(tmp_path)
    by_id = {
        model.data(model.index(r, 0), CatalogModel.MessageIdRole): r
        for r in range(model.rowCount())
    }
    delayed_row = by_id["3210447814"]  # issued Sat 11, uploaded Mon 20 → 6 wd
    assert model.data(model.index(delayed_row, 0), CatalogModel.DelayedRole) is True
    normal_row = by_id["3210447815"]  # issued Fri 3, uploaded Mon 6 → 1 wd
    assert model.data(model.index(normal_row, 0), CatalogModel.DelayedRole) is False


def test_money_and_pill_display(tmp_path: Path) -> None:
    model = _model(tmp_path)
    row = 1  # FCT-2107, 4821.50 RON, received
    assert (
        model.data(model.index(row, COL_TOTAL), Qt.ItemDataRole.DisplayRole)
        == "4.821,50 RON"
    )
    assert (
        model.data(model.index(row, COL_DIRECTION), CatalogModel.DirectionRole)
        == "received"
    )


def _cell(model: CatalogModel, message_id: str, col: int, role: int) -> object:
    for row in range(model.rowCount()):
        index = model.index(row, col)
        if model.data(index, CatalogModel.MessageIdRole) == message_id:
            return model.data(index, role)
    raise AssertionError(f"{message_id} not in the model")


def test_role_cif_columns_swap_with_direction(tmp_path: Path) -> None:
    model = _model(tmp_path)
    display = Qt.ItemDataRole.DisplayRole
    # A received invoice: the partner issues, we receive.
    assert _cell(model, "3210447811", COL_FROM_CIF, display) == "14338501"
    assert _cell(model, "3210447811", COL_TO_CIF, display) == OWN_CIFS[0]
    # The one sent invoice is the mirror.
    assert _cell(model, "3210447813", COL_FROM_CIF, display) == OWN_CIFS[0]
    assert _cell(model, "3210447813", COL_TO_CIF, display) == "22518743"


def test_own_cif_role_marks_whichever_side_is_ours(tmp_path: Path) -> None:
    """The delegate weights the followed CIF, so it must be told which cell."""
    model = _model(tmp_path)
    own = CatalogModel.OwnCifRole
    assert _cell(model, "3210447811", COL_TO_CIF, own) is True
    assert _cell(model, "3210447811", COL_FROM_CIF, own) is False
    assert _cell(model, "3210447813", COL_FROM_CIF, own) is True
    assert _cell(model, "3210447813", COL_TO_CIF, own) is False


def test_failing_row_knows_neither_cif(tmp_path: Path) -> None:
    """The failures table records no CIF — nothing was downloaded to read one."""
    model = _model(tmp_path)
    display = Qt.ItemDataRole.DisplayRole
    assert _cell(model, "3210447810", COL_FROM_CIF, display) == "—"
    assert _cell(model, "3210447810", COL_TO_CIF, display) == "—"
    assert _cell(model, "3210447810", COL_TO_CIF, CatalogModel.OwnCifRole) is False


def test_uploaded_column_renders_spv_date_and_dashes(tmp_path: Path) -> None:
    model = _model(tmp_path)
    by_id = {
        model.data(model.index(r, 0), CatalogModel.MessageIdRole): r
        for r in range(model.rowCount())
    }
    delayed = by_id["3210447814"]  # uploaded Monday 20 iul.
    assert (
        model.data(model.index(delayed, 1), Qt.ItemDataRole.DisplayRole) == "20.07.2026"
    )
    # The pinned failing row has no upload date until the message downloads.
    assert model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "—"


# -- model: filters -----------------------------------------------------------


def _filtered(tmp_path: Path, state: FilterState) -> list[str]:
    model = _model(tmp_path)
    model.set_filters(state.to_filters(_NOW.date()))
    return _ids(model)


def test_direction_filter_hides_the_other_direction(tmp_path: Path) -> None:
    ids = _filtered(tmp_path, FilterState(directions=frozenset({"received", FAILING})))
    assert "3210447813" not in ids  # the sent invoice excluded
    assert "3210447810" in ids  # "eșuată" still checked, so it stays pinned
    assert len(ids) == 5


def test_unchecking_failing_drops_the_pinned_row(tmp_path: Path) -> None:
    """ "Eșuată" is a value of the Direcție filter, not a separate switch."""
    ids = _filtered(tmp_path, FilterState(directions=frozenset({"received", "sent"})))
    assert "3210447810" not in ids
    assert len(ids) == 5


def test_delayed_only_keeps_just_the_late_invoice(tmp_path: Path) -> None:
    model = _model(tmp_path)
    model.set_filters(FilterState(delayed_only=True).to_filters(_NOW.date()))
    # Lateness is derived per row, so the result set is whole — and a failing
    # message, having no upload date at all, cannot be late.
    assert _ids(model) == ["3210447814"]
    assert model.canFetchMore(QModelIndex()) is False


def test_search_matches_and_hides_failing(tmp_path: Path) -> None:
    assert _filtered(tmp_path, FilterState(search="ACME")) == ["3210447815"]


def test_column_filters_narrow_one_column_each(tmp_path: Path) -> None:
    # Search spans number *or* partner; the column filters name just one.
    assert _filtered(tmp_path, FilterState(partner="ACME")) == ["3210447815"]
    assert _filtered(tmp_path, FilterState(number="ACME")) == []
    assert _filtered(tmp_path, FilterState(number="AS-1042")) == ["3210447813"]


def test_role_cif_filters_read_the_flow_not_the_sides(tmp_path: Path) -> None:
    """The followed CIF issues a sent invoice and receives a received one."""
    sent_from_us = _filtered(tmp_path, FilterState(from_cif=OWN_CIFS[0]))
    assert sent_from_us == ["3210447813"]  # AS-1042, the only trimisă
    to_us = _filtered(tmp_path, FilterState(to_cif=OWN_CIFS[0]))
    assert "3210447813" not in to_us
    assert set(to_us) == {"3210447811", "3210447814"}
    # And the partner's CIF mirrors it.
    assert _filtered(tmp_path, FilterState(to_cif="22518743")) == ["3210447813"]


def test_a_filter_a_failing_row_cannot_answer_unpins_it(tmp_path: Path) -> None:
    """Nothing downloaded means no number, no partner, no CIF, no dates."""
    for state in (
        FilterState(number="FCT"),
        FilterState(partner="ACME"),
        FilterState(from_cif="1234"),
        FilterState(issued=DateSpan(mode=THIS_MONTH)),
        FilterState(uploaded=DateSpan(mode=THIS_MONTH)),
    ):
        assert "3210447810" not in _filtered(tmp_path, state), state


def test_upload_date_filter_reads_created_at_not_the_issue_date(
    tmp_path: Path,
) -> None:
    window = DateSpan(CUSTOM, dt.date(2026, 7, 17), dt.date(2026, 7, 20))
    ids = _filtered(tmp_path, FilterState(uploaded=window))
    assert "3210447810" not in ids  # failing: no upload date at all
    assert "3210447815" not in ids  # uploaded 06.07, before the window
    # AS-1042 was *issued* on the 15th but uploaded on the 16th, so the two
    # date filters cannot be reading the same column.
    assert set(ids) == {"3210447811", "3210447812", "3210447814"}


# -- model: paging ------------------------------------------------------------
# (The delayed 5/6-day boundary itself is pinned in test_health.py, on
# `health.is_delayed` — the single rule the model and details pane share.)


def _seed_pages(path: Path, count: int = 150) -> None:
    with Archive.open(path) as archive:
        for i in range(count):
            archive.record(
                CatalogEntry(
                    message_id=f"m{i:03d}",
                    cif="1",
                    direction="received",
                    base_path=f"/a/{i}",
                    artifacts=["zip"],
                    issue_date=dt.date(2026, 7, 1) + dt.timedelta(days=i % 28),
                )
            )


def test_fetch_more_pages_the_catalog(tmp_path: Path) -> None:
    _seed_pages(tmp_path / "state.db")
    model = CatalogModel(tmp_path / "state.db", now=lambda: _NOW)
    assert model.rowCount() == 100
    assert model.canFetchMore(QModelIndex()) is True
    model.fetchMore(QModelIndex())
    assert model.rowCount() == 150
    assert model.canFetchMore(QModelIndex()) is False


def test_reload_keeps_paged_depth_but_new_filters_start_over(
    tmp_path: Path,
) -> None:
    # A refresh mid-scroll (sync commit, poll) must not collapse the catalog
    # back to the first page under the reader; picking a filter is a new list
    # and correctly starts back at one page.
    _seed_pages(tmp_path / "state.db")
    model = CatalogModel(tmp_path / "state.db", now=lambda: _NOW)
    model.fetchMore(QModelIndex())
    assert model.rowCount() == 150
    model.reload()
    assert model.rowCount() == 150
    model.set_filters(CatalogFilters())
    assert model.rowCount() == 100


def test_row_of_finds_loaded_rows_only(tmp_path: Path) -> None:
    _seed_pages(tmp_path / "state.db")
    model = CatalogModel(tmp_path / "state.db", now=lambda: _NOW)
    first_id = model.data(model.index(0, 0), CatalogModel.MessageIdRole)
    assert model.row_of(first_id) == 0
    assert model.row_of("never-archived") is None


# -- calendar range state machine --------------------------------------------


def test_range_calendar_two_clicks_emit_range(qtbot: object) -> None:
    cal = RangeCalendar()
    with qtbot.waitSignal(cal.range_selected, timeout=1000) as blocker:
        cal._pick(dt.date(2026, 7, 5))
        cal._pick(dt.date(2026, 7, 20))
    assert blocker.args == [dt.date(2026, 7, 5), dt.date(2026, 7, 20)]
    assert cal.selected_range() == (dt.date(2026, 7, 5), dt.date(2026, 7, 20))


def test_range_calendar_swaps_reversed_clicks(qtbot: object) -> None:
    cal = RangeCalendar()
    cal._pick(dt.date(2026, 7, 20))
    cal._pick(dt.date(2026, 7, 5))  # earlier than start → swapped
    assert cal.selected_range() == (dt.date(2026, 7, 5), dt.date(2026, 7, 20))


def test_range_calendar_third_click_starts_over(qtbot: object) -> None:
    cal = RangeCalendar()
    cal._pick(dt.date(2026, 7, 5))
    cal._pick(dt.date(2026, 7, 20))
    cal._pick(dt.date(2026, 7, 25))  # new range
    assert cal.selected_range() == (dt.date(2026, 7, 25), None)


# -- details path helper ------------------------------------------------------


def test_artifact_path_matches_engine_naming() -> None:
    assert artifact_path("/arch/2026/f1", ".pdf") == Path("/arch/2026/f1.pdf")
    # A dotted base keeps every dot-segment: `S.R.L` is a legal form, not an
    # extension. `Path.with_suffix` would file this as "ACME S.R.zip".
    assert artifact_path("/arch/ACME S.R.L", ".zip") == Path("/arch/ACME S.R.L.zip")
    assert artifact_path("/arch/FCT.1001", ".pdf") == Path("/arch/FCT.1001.pdf")


# -- window smoke -------------------------------------------------------------


def test_window_footer_counts_what_is_shown(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert "în arhivă" in win._footer.text()


def _click_header(win: MainWindow, section: int) -> None:
    """Click a section's *label*, through the real mouse path.

    Emitting ``sectionClicked`` instead would bypass ``mouseReleaseEvent`` —
    where QHeaderView flips its own sort indicator — and so test a gesture the
    user cannot perform. The same seam mistake CLAUDE.md records for QProcess.
    """
    header = win._header
    QTest.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(header.sectionViewportPosition(section) + 20, header.height() // 2),
    )


def _column(win: MainWindow, section: int) -> list[str]:
    model = win._model
    return [
        model.data(model.index(r, section), Qt.ItemDataRole.DisplayRole)
        for r in range(1, model.rowCount())  # row 0 is the pinned failure
    ]


def test_header_sorts_on_click_and_reverses_on_the_second(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()

    _click_header(win, COL_NUMBER)
    assert win._model.sort_column == COL_NUMBER
    # A first click on a new column sorts it descending, which is what the
    # catalog's own default order means.
    assert win._model.sort_order == Qt.SortOrder.DescendingOrder
    assert _column(win, COL_NUMBER) == sorted(_column(win, COL_NUMBER), reverse=True)

    _click_header(win, COL_NUMBER)
    assert win._model.sort_order == Qt.SortOrder.AscendingOrder
    assert _column(win, COL_NUMBER) == sorted(_column(win, COL_NUMBER))

    _click_header(win, COL_NUMBER)
    assert win._model.sort_order == Qt.SortOrder.DescendingOrder


def test_a_click_re_sorts_the_catalog_exactly_once(
    qtbot: object, tmp_path: Path
) -> None:
    """Qt's own click-to-sort path must not run alongside this header's.

    When it does, every click announces Qt's guess and then the correction —
    two model resets and two SQLite round-trips for one gesture.
    """
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    announced: list[tuple[int, Qt.SortOrder]] = []
    win._header.sortIndicatorChanged.connect(
        lambda section, order: announced.append((section, order))
    )
    _click_header(win, COL_NUMBER)
    assert announced == [(COL_NUMBER, Qt.SortOrder.DescendingOrder)]


def test_direction_column_refuses_to_sort(qtbot: object, tmp_path: Path) -> None:
    """Three values make a filter, not an order — the click does nothing.

    Including to the *indicator*: leaving it parked on an unsortable section
    shows no caret anywhere while the list keeps its old order, and saveState
    then persists that lie.
    """
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    before = win._model.sort_column
    _click_header(win, COL_DIRECTION)
    assert win._model.sort_column == before
    assert win._header.sortIndicatorSection() == before


def test_restoring_a_layout_cannot_revive_qt_click_to_sort(
    qtbot: object, tmp_path: Path
) -> None:
    """restoreState replays interaction flags, not just geometry."""
    seed_sample_archive(tmp_path / "state.db")
    first = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(first)
    first._header.setSectionsClickable(True)  # as a blob from any other build
    first.close()

    second = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(second)
    second.show()
    assert second._header.sectionsClickable() is False
    _click_header(second, COL_NUMBER)
    _click_header(second, COL_NUMBER)
    assert second._model.sort_order == Qt.SortOrder.AscendingOrder


def test_failing_row_stays_pinned_whatever_the_sort(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    for column in (COL_NUMBER, COL_TOTAL, COL_FROM_CIF):
        _click_header(win, column)
        assert _ids(win._model)[0] == "3210447810"


def test_window_selection_updates_details(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    # Select the FCT-2107 catalog row (row 1, after the pinned failing row).
    win._table.selectRow(1)
    assert win._details._current is not None
    assert getattr(win._details._current, "number", None) == "FCT-2107"


def test_window_refresh_keeps_scroll_anchor_and_selection(
    qtbot: object, tmp_path: Path
) -> None:
    # The reported bug: with 500+ invoices, every watcher-triggered refresh
    # reset the model, yanking the scrollbar and dropping the selection until
    # the list was unscrollable. A refresh must land the reader where they were.
    _seed_pages(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    table, model = win._table, win._model
    model.fetchMore(QModelIndex())
    table.selectRow(5)
    selected_id = model.data(model.index(5, 0), CatalogModel.MessageIdRole)
    table.scrollTo(model.index(120, 0), QAbstractItemView.ScrollHint.PositionAtTop)
    top_row = table.indexAt(QPoint(0, 0)).row()
    assert top_row > 100  # deep in the second page

    win.refresh()

    assert model.rowCount() == 150  # depth survived the reset
    assert table.indexAt(QPoint(0, 0)).row() == top_row
    current = table.selectionModel().currentIndex()
    assert model.data(current, CatalogModel.MessageIdRole) == selected_id
    assert getattr(win._details._current, "message_id", None) == selected_id


def test_search_field_filters_and_does_not_become_a_chip(
    qtbot: object, tmp_path: Path
) -> None:
    """Search is visible in its own field; echoing it in the bar says it twice."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._search.setText("ACME")
    assert _ids(win._model) == ["3210447815"]
    assert win._filter_bar.isVisibleTo(win) is False


def test_active_filter_bar_appears_with_a_filter_and_removes_it(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert win._filter_bar.isVisibleTo(win) is False

    win._filters = FilterState(partner="ACME")
    win._apply_filters()
    assert win._filter_bar.isVisibleTo(win) is True
    assert win._header.funnel_is_active(COL_PARTNER) is True
    assert _ids(win._model) == ["3210447815"]

    win._filter_bar.chip_removed.emit(KEY_PARTNER)
    assert win._filter_bar.isVisibleTo(win) is False
    assert win._header.funnel_is_active(COL_PARTNER) is False
    assert len(_ids(win._model)) == 6


def test_clearing_every_filter_leaves_the_search_alone(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._search.setText("S.R.L.")
    win._filters = dataclasses.replace(win._filters, partner="ACME")
    win._apply_filters()
    win._filter_bar.cleared.emit()
    assert win._filters.partner == ""
    assert win._filters.search == "S.R.L."
    assert win._search.text() == "S.R.L."


# -- elastic layout + geometry persistence -------------------------------------


def test_window_design_size_is_the_minimum(qtbot: object, tmp_path: Path) -> None:
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    # The width floor is *derived* (the columns measure themselves in the
    # platform's font), so it is at least the design size, never below it.
    assert win.minimumWidth() >= 1160
    assert win.minimumHeight() == 620
    win.resize(1400, 900)  # a fixed-size window would refuse this
    assert (win.width(), win.height()) == (1400, 900)


def test_window_geometry_persists_across_instances(
    qtbot: object, tmp_path: Path
) -> None:
    # QSettings is redirected to a throwaway ini dir by conftest.
    first = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(first)
    first.resize(1000, 700)
    first.close()  # closeEvent saves the geometry

    second = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(second)
    # The offscreen test screen is 800×800 (hardcoded in the Qt plugin) —
    # narrower than the design minimum. Height round-trips through QSettings;
    # width shows the other half of the design: restoreGeometry clamps to the
    # available screen (detached-monitor recovery) and the minimum floors it.
    assert second.height() == 700
    # Width shows the other half of the design: restoreGeometry clamps to the
    # available screen and the (derived) minimum floors it.
    assert second.width() == second.minimumWidth()


# -- resizable columns ---------------------------------------------------------


def test_partener_stretches_and_the_rest_are_user_resizable(
    qtbot: object, tmp_path: Path
) -> None:
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    header = win._table.horizontalHeader()
    # Partener is the stretch section, so every drag is a zero-sum trade and
    # the table can never exceed its viewport (DESIGN.md §10).
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Stretch
    for col in (0, 1, 2, 4, 5):
        assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive


def test_data_column_fits_a_full_zz_ll_aaaa_date(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    rendered = win._model.data(win._model.index(1, 0), Qt.ItemDataRole.DisplayRole)
    assert rendered == "18.07.2026"
    metrics = win._table.fontMetrics()
    assert win._table.columnWidth(0) >= metrics.horizontalAdvance(rendered)


def test_column_widths_persist_across_instances(qtbot: object, tmp_path: Path) -> None:
    first = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(first)
    first._table.setColumnWidth(1, 140)
    first.close()  # closeEvent saves geometry *and* header layout

    second = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(second)
    assert second._table.columnWidth(1) == 140


# -- the two-window split ------------------------------------------------------


def test_settings_button_only_asks_the_tray_to_open_setari(
    qtbot: object, tmp_path: Path
) -> None:
    # Facturi never hosts the form: it has no stack and no nav (DESIGN.md §10).
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert not win.findChildren(QStackedWidget)
    with qtbot.waitSignal(win.settings_requested, timeout=1000):
        win._settings_button.click()


def test_fixed_columns_fit_their_widest_value_in_the_real_font(
    qtbot: object, tmp_path: Path
) -> None:
    # The handoff's px were measured in a browser at 13px, so they are a floor:
    # a platform with wider metrics must still not clip a date or a total.
    from anaf_sync.tray.delegates import PAD_EDGE, PAD_X
    from anaf_sync.tray.window import _COL_SAMPLES, _LAST_COL

    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    metrics = win._table.fontMetrics()
    for col, sample in _COL_SAMPLES.items():
        padding = (PAD_EDGE if col == 0 else PAD_X) + (
            PAD_EDGE if col == _LAST_COL else PAD_X
        )
        assert win._table.columnWidth(col) >= (
            metrics.horizontalAdvance(sample) + padding
        )


# -- per-invoice reprocess ------------------------------------------------------


def _unreadable_entry(**overrides: object) -> CatalogEntry:
    """A row as an unreadable projection leaves it: no number, date or partner."""
    fields: dict[str, object] = {
        "message_id": "4001",
        "cif": "12345678",
        "direction": "received",
        "base_path": "/archive/unknown/unknown_unknown",
        "artifacts": ["zip"],
        "message_type": "FACTURA PRIMITA",
    }
    return CatalogEntry(**{**fields, **overrides})  # type: ignore[arg-type]


def test_unreadable_row_is_recognised_by_all_three_blanks() -> None:
    from anaf_sync.tray.details import is_unreadable

    assert is_unreadable(_unreadable_entry())
    # One missing field alone is an ordinary invoice, not a broken projection.
    assert not is_unreadable(
        _unreadable_entry(number="1882", issue_date=dt.date.today())
    )


def test_reprocess_button_asks_for_the_selected_message(
    qtbot: object, tmp_path: Path
) -> None:
    asked: list[str] = []
    win = MainWindow(state_path=tmp_path / "state.db", on_reprocess=asked.append)
    qtbot.addWidget(win)

    win._details.show_record(_unreadable_entry())
    win._details._reprocess_button.click()

    assert asked == ["4001"]


def test_reprocess_button_is_promoted_only_where_it_repairs(
    qtbot: object, tmp_path: Path
) -> None:
    from anaf_sync.tray.details import _REPROCESS_LABEL

    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)

    win._details.show_record(_unreadable_entry())
    assert win._details._reprocess_button.text() == _REPROCESS_LABEL
    # The panel that explains the blanks — and that the invoice itself is safe.
    assert _pane_text(win._details, "Câmpuri necitite")

    win._details.show_record(
        _unreadable_entry(number="1882", issue_date=dt.date(2026, 6, 17))
    )
    assert win._details._reprocess_button is not None  # still offered, quietly
    assert not _pane_text(win._details, "Câmpuri necitite")


def test_reprocess_is_not_offered_for_backfilled_rows(
    qtbot: object, tmp_path: Path
) -> None:
    """`reprocess` excludes them by construction — an enabled button would lie."""
    asked: list[str] = []
    win = MainWindow(state_path=tmp_path / "state.db", on_reprocess=asked.append)
    qtbot.addWidget(win)

    win._details.show_record(
        _unreadable_entry(message_id="backfill:abc", source="backfill")
    )

    assert win._details._reprocess_button is None  # nothing busy-state may touch
    button = _find_button(win._details, "Recitește")
    assert button is not None and not button.isEnabled()


def test_busy_state_disables_the_button_and_says_so(
    qtbot: object, tmp_path: Path
) -> None:
    from anaf_sync.tray.details import _REPROCESS_BUSY, _REPROCESS_LABEL

    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._details.show_record(_unreadable_entry())

    win.set_busy(True)
    assert win._details._reprocess_button.text() == _REPROCESS_BUSY
    assert not win._details._reprocess_button.isEnabled()

    # A selection change mid-run must not hand back an enabled button.
    win._details.show_record(_unreadable_entry(message_id="4002"))
    assert not win._details._reprocess_button.isEnabled()

    win.set_busy(False)
    assert win._details._reprocess_button.text() == _REPROCESS_LABEL
    assert win._details._reprocess_button.isEnabled()


def _live_widgets(pane: object) -> list[object]:
    """Every widget currently in the pane's layout.

    Not `findChildren`: the pane rebuilds through `clear_layout`, which uses
    `deleteLater`, so the previous record's widgets are still children until the
    event loop runs — and a test asserting a panel is *gone* would find its
    ghost.
    """
    found: list[object] = []
    stack = [pane._layout]
    while stack:
        layout = stack.pop()
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if (widget := item.widget()) is not None:
                found.append(widget)
                if (nested := widget.layout()) is not None:
                    stack.append(nested)
            elif (nested_layout := item.layout()) is not None:
                stack.append(nested_layout)
    return found


def _find_button(pane: object, text: str) -> object:
    from PySide6.QtWidgets import QPushButton

    for widget in _live_widgets(pane):
        if isinstance(widget, QPushButton) and text in widget.text():
            return widget
    return None


def _pane_text(pane: object, needle: str) -> bool:
    from PySide6.QtWidgets import QLabel

    return any(
        isinstance(widget, QLabel) and needle in widget.text()
        for widget in _live_widgets(pane)
    )


# -- header: the ▽ is its own hit target --------------------------------------


def test_funnel_sits_clear_of_the_resize_handle(qtbot: object, tmp_path: Path) -> None:
    """Overlapping it would make "grab the edge to resize" miss."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    header = win._header
    for section in range(win._model.columnCount()):
        if not header.is_filterable(section):
            continue
        boundary = header.sectionViewportPosition(section) + header.sectionSize(section)
        assert header.funnel_rect(section).right() <= boundary - FUNNEL_MARGIN


def test_total_carries_no_funnel(qtbot: object, tmp_path: Path) -> None:
    """An amount range is a form, not a popover — and the gap says so."""
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert win._header.is_filterable(COL_TOTAL) is False
    assert win._header.is_sortable(COL_TOTAL) is True


def test_clicking_the_funnel_asks_for_a_filter_not_a_sort(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    before = win._model.sort_column
    asked: list[int] = []
    win._header.filter_requested.connect(asked.append)

    point = win._header.funnel_rect(COL_NUMBER).center()
    QTest.mouseClick(win._header.viewport(), Qt.MouseButton.LeftButton, pos=point)

    assert asked == [COL_NUMBER]
    assert win._model.sort_column == before  # the press never reached the sort


# -- header filters: the popovers ---------------------------------------------


def test_text_popover_filters_live_without_an_ok_button(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._open_filter(COL_PARTNER)
    popup = win._popup
    assert isinstance(popup, TextFilterPopup)
    popup._field.setText("ACME")
    assert _ids(win._model) == ["3210447815"]
    assert win._filters.partner == "ACME"


def test_cif_popover_offers_the_followed_cifs_with_matching_counts(
    qtbot: object, tmp_path: Path
) -> None:
    """The count beside a shortcut is exactly what clicking it yields."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._open_filter(COL_TO_CIF)
    popup = win._popup
    assert isinstance(popup, CifFilterPopup)

    counts = dict(win._own_cif_counts(COL_TO_CIF))
    assert set(counts) == set(OWN_CIFS)
    popup._pick(OWN_CIFS[0])
    # Two received invoices name 12345678 as recipient; the sent one does not.
    assert len(_ids(win._model)) == counts[OWN_CIFS[0]] == 2
    assert "3210447813" not in _ids(win._model)


def test_direction_popover_counts_failures_from_the_other_table(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    counts = {value: count for value, _label, count in win._direction_options()}
    assert counts == {"received": 4, "sent": 1, FAILING: 1}


def test_direction_popover_refuses_to_uncheck_the_last_value(
    qtbot: object, tmp_path: Path
) -> None:
    """An empty table whose only way back is "Șterge filtrul" is a trap."""
    popup = ChecklistFilterPopup(
        options=[("received", "primită", 4), ("sent", "trimisă", 1)],
        checked=frozenset({"sent"}),
    )
    qtbot.addWidget(popup)
    popup._boxes["sent"].setChecked(False)
    popup._toggled("sent")
    assert popup.checked == frozenset({"sent"})  # sprang back, visibly


def test_delayed_checkbox_rides_in_the_uploaded_popover(
    qtbot: object, tmp_path: Path
) -> None:
    """Lateness is a fact about the upload date, so it belongs to that column."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)

    win._open_filter(COL_ISSUED)
    assert isinstance(win._popup, DateFilterPopup)
    assert win._popup._delayed is None  # not on Emisă

    win._open_filter(COL_UPLOADED)
    popup = win._popup
    assert isinstance(popup, DateFilterPopup)
    assert popup._delayed is not None
    popup._delayed.setChecked(True)
    popup.changed.emit()
    assert win._filters.delayed_only is True
    assert _ids(win._model) == ["3210447814"]


def test_date_popover_reveals_the_calendar_only_for_a_custom_span(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._open_filter(COL_ISSUED)
    popup = win._popup
    assert isinstance(popup, DateFilterPopup)
    popup.show()
    assert popup._calendar.isVisibleTo(popup) is False
    popup._buttons[CUSTOM].setChecked(True)
    popup._pick_mode(CUSTOM)
    assert popup._calendar.isVisibleTo(popup) is True
    assert win._filters.issued.mode == CUSTOM


# -- the details pane collapses on its own ------------------------------------


def test_pane_is_folded_until_something_is_selected(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert win._details_panel.isVisibleTo(win) is False
    assert win._rail.isVisibleTo(win) is True
    assert win._expand_button.isEnabled() is False  # nothing to expand to

    win._table.selectRow(1)
    assert win._details_panel.isVisibleTo(win) is True
    assert win._rail.isVisibleTo(win) is False


def test_pane_can_be_pinned_shut_and_reopened(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._table.selectRow(1)

    win._collapse_button.click()
    assert win._details_panel.isVisibleTo(win) is False
    assert win._expand_button.isEnabled() is True  # the row is still selected
    assert win._selected_id() == "3210447811"

    win._expand_button.click()
    assert win._details_panel.isVisibleTo(win) is True


def test_a_pinned_pane_stays_shut_across_selections(
    qtbot: object, tmp_path: Path
) -> None:
    """Folding it is a preference about the layout, not a hint about one row.

    Reopening on the next selection would make the ``›`` a one-row undo and the
    persisted state meaningless — the rail's ``‹`` is the way back.
    """
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._table.selectRow(1)
    win._collapse_button.click()
    win._table.selectRow(2)
    assert win._details_panel.isVisibleTo(win) is False
    assert win._expand_button.isEnabled() is True


def test_pinned_shut_survives_a_restart(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    first = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(first)
    first._table.selectRow(1)
    first._collapse_button.click()
    first.close()

    second = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(second)
    second._table.selectRow(1)
    assert second._details_panel.isVisibleTo(second) is False


def test_selection_survives_a_filter_that_keeps_its_row(
    qtbot: object, tmp_path: Path
) -> None:
    """Clearing it on every change would throw away a pane mid-read."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._table.selectRow(1)  # FCT-2107
    assert win._selected_id() == "3210447811"

    win._search.setText("ELECTROMONTAJ")
    assert win._selected_id() == "3210447811"
    assert win._details_panel.isVisibleTo(win) is True


def test_a_filter_that_hides_the_selected_row_folds_the_pane(
    qtbot: object, tmp_path: Path
) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._table.selectRow(1)  # FCT-2107

    win._search.setText("ACME")
    assert win._selected_id() is None
    assert win._details._current is None
    assert win._details_panel.isVisibleTo(win) is False


def test_reclicking_the_selected_row_deselects_it(
    qtbot: object, tmp_path: Path
) -> None:
    """Qt's single-selection mode offers no other way out of a selection."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    table = win._table
    point = table.visualRect(win._model.index(1, COL_NUMBER)).center()

    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert win._selected_id() == "3210447811"

    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert win._selected_id() is None
    assert win._details_panel.isVisibleTo(win) is False


def test_funnel_lights_under_the_pointer_and_clears_on_leave(
    qtbot: object, tmp_path: Path
) -> None:
    """The hover is what tells a reader the ▽ is not part of the label."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    header = win._header

    QTest.mouseMove(header.viewport(), header.funnel_rect(COL_NUMBER).center())
    assert header.hovered_funnel == COL_NUMBER

    # The label half of the same section is a different target.
    label_side = QPoint(header.sectionViewportPosition(COL_NUMBER) + 4, 8)
    QTest.mouseMove(header.viewport(), label_side)
    assert header.hovered_funnel == -1


def test_total_has_no_funnel_to_hover(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    header = win._header
    right_edge = (
        header.sectionViewportPosition(COL_TOTAL) + header.sectionSize(COL_TOTAL) - 8
    )
    assert header.funnel_at(QPoint(right_edge, 8)) == -1


def test_partener_still_readable_at_the_minimum_width(
    qtbot: object, tmp_path: Path
) -> None:
    """The point of deriving the floor: a constant one squeezes Partener hardest
    on exactly the machines whose font metrics are widest."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    # Resize *after* show: the offscreen platform's screen is 800×800, so a
    # window sized before it is mapped comes back clamped.
    win.resize(win.minimumWidth(), win.minimumHeight())
    win._table.selectRow(1)  # the details pane open is the tighter case
    # The rail and the pane are never both up, but the layout gives the rail's
    # 30px back a frame after the pane appears — so wait for the geometry, not
    # just for the visibility that triggers it.
    qtbot.waitUntil(lambda: win._table.columnWidth(COL_PARTNER) >= 190, timeout=1000)


def test_switching_preset_then_custom_keeps_the_preset_range(
    qtbot: object, tmp_path: Path
) -> None:
    """The radio has flipped by the time its handler runs, so the popup must
    remember the mode it is leaving rather than ask which one is checked."""
    popup = DateFilterPopup(span=DateSpan(mode=ALL), today=_NOW.date())
    qtbot.addWidget(popup)

    popup._buttons[THIS_MONTH].setChecked(True)
    popup._pick_mode(THIS_MONTH)
    popup._buttons[CUSTOM].setChecked(True)
    popup._pick_mode(CUSTOM)

    # "Personalizat…" narrows what is on screen; it does not reset to today.
    assert popup.span == DateSpan(CUSTOM, dt.date(2026, 7, 1), dt.date(2026, 7, 31))


def test_custom_from_the_unfiltered_state_seeds_the_current_month(
    qtbot: object, tmp_path: Path
) -> None:
    popup = DateFilterPopup(span=DateSpan(mode=ALL), today=_NOW.date())
    qtbot.addWidget(popup)
    popup._buttons[CUSTOM].setChecked(True)
    popup._pick_mode(CUSTOM)
    assert popup.span == DateSpan(CUSTOM, dt.date(2026, 7, 1), dt.date(2026, 7, 31))


def test_total_caret_takes_the_slot_its_padding_reserves(
    qtbot: object, tmp_path: Path
) -> None:
    """Total has no funnel, so its caret sits in the outer slot — otherwise the
    right-aligned label runs under its own indicator."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.show()
    _click_header(win, COL_TOTAL)
    assert win._header.sortIndicatorSection() == COL_TOTAL

    section = QRect(0, 0, win._table.columnWidth(COL_TOTAL), win._header.height())
    caret = win._header._mark_rect(
        section, outer=not win._header.is_filterable(COL_TOTAL)
    )
    # The QSS keeps the label clear of everything right of this point.
    assert section.right() - caret.left() <= MARK_SLOT


def test_resizing_a_column_does_not_also_sort_it(qtbot: object, tmp_path: Path) -> None:
    """A boundary drag ends on a section like a click does, so the release has
    to tell them apart or every resize re-sorts the neighbouring column."""
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win.resize(1360, 500)
    win.show()
    header = win._header
    sorted_by = win._model.sort_column
    before = header.sectionSize(COL_NUMBER)

    boundary = header.sectionViewportPosition(COL_NUMBER) + before
    y = header.height() // 2
    QTest.mousePress(
        header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(boundary, y)
    )
    QTest.mouseMove(header.viewport(), QPoint(boundary + 40, y))
    QTest.mouseRelease(
        header.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(boundary + 40, y)
    )

    assert header.sectionSize(COL_NUMBER) > before  # the resize took effect
    assert win._model.sort_column == sorted_by  # and nothing else did
