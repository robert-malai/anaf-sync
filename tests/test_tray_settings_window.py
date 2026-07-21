"""The Setări window: its size range, its two exits, and its own geometry key."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QDialog  # noqa: E402

from anaf_sync.config import write_default_config  # noqa: E402
from anaf_sync.tray import settings_view as sv  # noqa: E402
from anaf_sync.tray.flowgrid import (  # noqa: E402
    MIN_COLUMN_WIDTH,
    SPACING,
    column_count,
)
from anaf_sync.tray.settings_window import SettingsWindow  # noqa: E402
from anaf_sync.tray.window import MainWindow  # noqa: E402


@pytest.fixture(autouse=True)
def _no_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sv, "schedule_status", lambda: "not installed")


def _window(tmp_path: Path) -> SettingsWindow:
    config = tmp_path / "config.toml"
    if not config.exists():  # a second window over the same config, by design
        write_default_config(config)
    return SettingsWindow(state_path=tmp_path / "state.db", config_path=config)


# -- size range ---------------------------------------------------------------


def test_setari_is_resizable_between_its_design_size_and_a_maximum(
    qtbot: object, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    qtbot.addWidget(window)
    assert (window.minimumWidth(), window.minimumHeight()) == (760, 620)
    assert (window.maximumWidth(), window.maximumHeight()) == (1200, 780)

    window.resize(1000, 700)  # freely resizable in between
    assert (window.width(), window.height()) == (1000, 700)

    window.resize(4000, 4000)  # a form past its ceiling is only empty space
    assert (window.width(), window.height()) == (1200, 780)


def test_facturi_has_no_maximum(qtbot: object, tmp_path: Path) -> None:
    # The asymmetry is the point: a catalog can always use more room.
    win = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(win)
    assert win.maximumWidth() >= 4000


# -- the two exits ------------------------------------------------------------


def test_cancel_closes_without_writing(qtbot: object, tmp_path: Path) -> None:
    window = _window(tmp_path)
    qtbot.addWidget(window)
    config = tmp_path / "config.toml"
    original = config.read_text(encoding="utf-8")
    window.open_fresh()

    window._view._lookback.setValue(30)
    window._view.cancelled.emit()  # what "Renunță" / Esc / the × all do

    assert not window.isVisible()
    assert config.read_text(encoding="utf-8") == original


def test_reopening_after_cancel_leaves_no_residue(
    qtbot: object, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    qtbot.addWidget(window)
    window.open_fresh()
    window._view._lookback.setValue(30)
    window._view.cancelled.emit()

    window.open_fresh()  # re-reads config.toml, so the pending 30 is gone
    assert window._view._lookback.value() == 60


def test_save_writes_then_closes_and_announces(qtbot: object, tmp_path: Path) -> None:
    window = _window(tmp_path)
    qtbot.addWidget(window)
    window.open_fresh()
    window._view._lookback.setValue(30)

    with qtbot.waitSignal(window.saved, timeout=1000):
        window._view._save()

    assert not window.isVisible()
    assert "lookback_days = 30" in (tmp_path / "config.toml").read_text(
        encoding="utf-8"
    )


def test_escape_rejects_the_dialog(qtbot: object, tmp_path: Path) -> None:
    # QDialog's reject path is why this is a QDialog at all.
    window = _window(tmp_path)
    qtbot.addWidget(window)
    window.open_fresh()
    window.reject()
    assert not window.isVisible()
    assert window.result() == QDialog.DialogCode.Rejected


# -- geometry keys ------------------------------------------------------------


def test_setari_geometry_is_its_own_key_not_the_catalog_s(
    qtbot: object, tmp_path: Path
) -> None:
    catalog = MainWindow(state_path=tmp_path / "state.db")
    qtbot.addWidget(catalog)
    catalog.resize(1400, 700)
    catalog.close()

    window = _window(tmp_path)
    qtbot.addWidget(window)
    # Remembering Setări at the catalog's dimensions would be a bug, not a
    # convenience — and 1400 is past its maximum anyway.
    assert window.width() <= 1200


def test_setari_geometry_persists_across_instances(
    qtbot: object, tmp_path: Path
) -> None:
    first = _window(tmp_path)
    qtbot.addWidget(first)
    first.resize(900, 700)
    first.close()

    second = _window(tmp_path)
    qtbot.addWidget(second)
    assert second.height() == 700


# -- the artifact grid's re-flow rule -----------------------------------------


def test_artifact_grid_flows_three_up_then_five_up() -> None:
    threshold = 5 * MIN_COLUMN_WIDTH + 4 * SPACING
    assert column_count(threshold) == 5
    assert column_count(threshold - 1) == 3
    assert column_count(300) == 3


def test_artifact_grid_never_uses_four_columns() -> None:
    # Five cards in four columns strand one alone on the second row.
    assert 4 not in {column_count(width) for width in range(200, 1400)}


def test_artifact_cards_lay_out_in_two_rows_then_one(
    qtbot: object, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    qtbot.addWidget(window)
    grid = window._view._artifact_boxes["zip"].parent().parent().layout()

    narrow, wide = 560, 5 * MIN_COLUMN_WIDTH + 4 * SPACING
    assert grid.heightForWidth(wide) < grid.heightForWidth(narrow)  # 1 row vs 2
