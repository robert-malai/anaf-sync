"""Qt-dependent Facturi pieces: model, calendar, delegate, details, window."""

import datetime as dt
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QHeaderView,
    QStackedWidget,
)
from sample_data import seed_sample_archive  # noqa: E402

from anaf_sync.state import Archive, CatalogEntry  # noqa: E402
from anaf_sync.tray.calendar import RangeCalendar  # noqa: E402
from anaf_sync.tray.details import artifact_path  # noqa: E402
from anaf_sync.tray.models import CatalogFilters, CatalogModel  # noqa: E402
from anaf_sync.tray.window import MainWindow, _month_end  # noqa: E402

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
        model.data(model.index(row, 5), Qt.ItemDataRole.DisplayRole) == "4.821,50 RON"
    )
    assert model.data(model.index(row, 4), CatalogModel.DirectionRole) == "received"


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


def test_direction_filter_hides_failing_and_other_direction(tmp_path: Path) -> None:
    model = _model(tmp_path)
    model.set_filters(CatalogFilters(direction="received"))
    ids = _ids(model)
    assert "3210447810" not in ids  # failing hidden when a direction is chosen
    assert "3210447813" not in ids  # the sent invoice excluded
    assert len(ids) == 4


def test_problems_only_shows_failing_and_delayed(tmp_path: Path) -> None:
    model = _model(tmp_path)
    model.set_filters(CatalogFilters(problems_only=True))
    ids = set(_ids(model))
    assert ids == {"3210447810", "3210447814"}  # failing + delayed
    assert model.canFetchMore(QModelIndex()) is False


def test_search_matches_and_hides_failing(tmp_path: Path) -> None:
    model = _model(tmp_path)
    model.set_filters(CatalogFilters(search="ACME"))
    assert _ids(model) == ["3210447815"]


def test_problem_count_counts_failing_plus_delayed(tmp_path: Path) -> None:
    model = _model(tmp_path)
    assert model.problem_count() == 2  # 1 failing + 1 delayed


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


def test_month_end() -> None:
    assert _month_end(dt.date(2026, 7, 10)) == dt.date(2026, 7, 31)
    assert _month_end(dt.date(2026, 2, 3)) == dt.date(2026, 2, 28)
    assert _month_end(dt.date(2026, 12, 1)) == dt.date(2026, 12, 31)


# -- window smoke -------------------------------------------------------------


def test_window_footer_and_problem_chip(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert "în arhivă" in win._footer.text()
    assert win._chip_problems.text() == "Probleme (2)"


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


def test_window_direction_chip_filters(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    win._chip_received.click()
    ids = _ids(win._model)
    assert "3210447813" not in ids  # the sent invoice gone
    assert "3210447810" not in ids  # failing hidden under a direction filter


# -- elastic layout + geometry persistence -------------------------------------


def test_window_design_size_is_the_minimum(qtbot: object, tmp_path: Path) -> None:
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert (win.minimumWidth(), win.minimumHeight()) == (980, 620)
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
    assert second.width() == 980


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
