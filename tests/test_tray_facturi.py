"""Qt-dependent Facturi pieces: model, calendar, delegate, details, window."""

import datetime as dt
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
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
    delayed_row = by_id["3210447814"]  # issued 11, uploaded 19 → 8 days
    assert model.data(model.index(delayed_row, 0), CatalogModel.DelayedRole) is True
    normal_row = by_id["3210447815"]  # issued 3, uploaded 6 → 3 days
    assert model.data(model.index(normal_row, 0), CatalogModel.DelayedRole) is False


def test_money_and_pill_display(tmp_path: Path) -> None:
    model = _model(tmp_path)
    row = 1  # FCT-2107, 4821.50 RON, received
    assert (
        model.data(model.index(row, 4), Qt.ItemDataRole.DisplayRole) == "4.821,50 RON"
    )
    assert model.data(model.index(row, 3), CatalogModel.DirectionRole) == "received"


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


# -- model: delayed boundary + paging ----------------------------------------


def _delayed(issue: dt.date, created: dt.datetime) -> bool:
    from anaf_sync.tray.models import _is_delayed

    entry = CatalogEntry(
        message_id="x",
        cif="1",
        direction="received",
        base_path="p",
        artifacts=[],
        issue_date=issue,
        created_at=created,
    )
    return _is_delayed(entry)


def test_delayed_boundary_exactly_five_days_is_not_delayed(tmp_path: Path) -> None:
    assert _delayed(dt.date(2026, 7, 1), dt.datetime(2026, 7, 6)) is False  # 5 days
    assert _delayed(dt.date(2026, 7, 1), dt.datetime(2026, 7, 7)) is True  # 6 days


def test_fetch_more_pages_the_catalog(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        for i in range(150):
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
    model = CatalogModel(tmp_path / "state.db", now=lambda: _NOW)
    assert model.rowCount() == 100
    assert model.canFetchMore(QModelIndex()) is True
    model.fetchMore(QModelIndex())
    assert model.rowCount() == 150
    assert model.canFetchMore(QModelIndex()) is False


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
    # Dotted base name: identical to the engine's own base.with_suffix(".zip"),
    # which replaces from the last dot — the point is they agree, byte-for-byte.
    assert artifact_path("/arch/ACME S.R.L", ".zip") == Path("/arch/ACME S.R.zip")


def test_month_end() -> None:
    assert _month_end(dt.date(2026, 7, 10)) == dt.date(2026, 7, 31)
    assert _month_end(dt.date(2026, 2, 3)) == dt.date(2026, 2, 28)
    assert _month_end(dt.date(2026, 12, 1)) == dt.date(2026, 12, 31)


# -- window smoke -------------------------------------------------------------


def test_window_footer_and_problem_chip(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db", config_path=tmp_path / "c.toml")
    qtbot.addWidget(win)
    assert "în arhivă" in win._footer.text()
    assert win._chip_problems.text() == "Probleme (2)"


def test_window_selection_updates_details(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db", config_path=tmp_path / "c.toml")
    qtbot.addWidget(win)
    # Select the FCT-2107 catalog row (row 1, after the pinned failing row).
    win._table.selectRow(1)
    assert win._details._current is not None
    assert getattr(win._details._current, "number", None) == "FCT-2107"


def test_window_direction_chip_filters(qtbot: object, tmp_path: Path) -> None:
    seed_sample_archive(tmp_path / "state.db")
    win = MainWindow(state_path=tmp_path / "state.db", config_path=tmp_path / "c.toml")
    qtbot.addWidget(win)
    win._chip_received.click()
    ids = _ids(win._model)
    assert "3210447813" not in ids  # the sent invoice gone
    assert "3210447810" not in ids  # failing hidden under a direction filter


# -- elastic layout + geometry persistence -------------------------------------


def test_window_design_size_is_the_minimum(qtbot: object, tmp_path: Path) -> None:
    win = MainWindow(state_path=tmp_path / "state.db", config_path=tmp_path / "c.toml")
    qtbot.addWidget(win)
    assert (win.minimumWidth(), win.minimumHeight()) == (980, 620)
    win.resize(1400, 900)  # a fixed-size window would refuse this
    assert (win.width(), win.height()) == (1400, 900)


def test_window_geometry_persists_across_instances(
    qtbot: object, tmp_path: Path
) -> None:
    # QSettings is redirected to a throwaway ini dir by conftest.
    first = MainWindow(
        state_path=tmp_path / "state.db", config_path=tmp_path / "c.toml"
    )
    qtbot.addWidget(first)
    first.resize(1000, 700)
    first.close()  # closeEvent saves the geometry

    second = MainWindow(
        state_path=tmp_path / "state.db", config_path=tmp_path / "c.toml"
    )
    qtbot.addWidget(second)
    # The offscreen test screen is 800×800 (hardcoded in the Qt plugin) —
    # narrower than the design minimum. Height round-trips through QSettings;
    # width shows the other half of the design: restoreGeometry clamps to the
    # available screen (detached-monitor recovery) and the minimum floors it.
    assert second.height() == 700
    assert second.width() == 980
