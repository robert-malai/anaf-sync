"""Qt-dependent tray pieces: icons, theme QSS, the tray app, watcher, runner.

Skipped entirely when the ``tray`` extra (PySide6) is absent, so the core suite
still passes without it. Runs headless via ``QT_QPA_PLATFORM=offscreen`` (set in
conftest).
"""

import datetime as dt
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from anaf_sync.config import write_default_config  # noqa: E402
from anaf_sync.state import Archive, RunRecord  # noqa: E402
from anaf_sync.tray.app import (  # noqa: E402
    MENU_ARCHIVED_INVOICES,
    MENU_SYNC_NOW,
    MENU_SYNCING,
    TrayApp,
)
from anaf_sync.tray.icons import status_icon  # noqa: E402
from anaf_sync.tray.runner import SyncRunner  # noqa: E402
from anaf_sync.tray.status import (  # noqa: E402
    ARCHIVE_UP_TO_DATE,
    NEEDS_ATTENTION,
    TrayStatus,
)
from anaf_sync.tray.theme import DARK, LIGHT, menu_qss, status_color  # noqa: E402
from anaf_sync.tray.watcher import StateWatcher  # noqa: E402

_NOW = dt.datetime(2026, 7, 20, 12, 0, tzinfo=dt.UTC)


# -- icons --------------------------------------------------------------------


@pytest.mark.parametrize("state", ["ok", "warn", "err"])
def test_status_icon_renders_all_sizes(qtbot: object, state: str) -> None:
    icon = status_icon(state, theme=LIGHT)
    assert not icon.isNull()
    assert len(icon.availableSizes()) == 3


# -- theme --------------------------------------------------------------------


def test_menu_qss_uses_theme_tokens() -> None:
    qss = menu_qss(LIGHT)
    assert LIGHT.panel_bg in qss
    assert LIGHT.accent in qss
    assert menu_qss(DARK) != qss  # the two schemes differ


def test_status_color_maps_states() -> None:
    assert status_color(LIGHT, "ok") == LIGHT.green
    assert status_color(LIGHT, "warn") == LIGHT.amber
    assert status_color(LIGHT, "err") == LIGHT.red


# -- TrayApp ------------------------------------------------------------------


def _app(tmp_path: Path) -> TrayApp:
    config = tmp_path / "config.toml"
    write_default_config(config)
    return TrayApp(state_path=tmp_path / "state.db", config_path=config)


def _status(*, subline: str) -> TrayStatus:
    return TrayStatus(
        state="ok",
        headline=ARCHIVE_UP_TO_DATE,
        subline=subline,
        alert_text=None,
        alert_command=None,
        alert_state="ok",
        archived_count=10,
        output_dir=None,
    )


def test_tray_app_ok_state(qtbot: object, tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record_run(RunRecord(finished_at=_NOW, outcome="ok", archived=2))
    app = _app(tmp_path)
    app.refresh()
    assert ARCHIVE_UP_TO_DATE in app._headline_label.text()
    assert app._alert_frame.isHidden()
    assert app._folder_action.isEnabled()  # config valid → dir known


def test_tray_app_warn_state_shows_alert(qtbot: object, tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record_failure("m9", "HTTP 500")
    app = _app(tmp_path)
    app.refresh()
    assert NEEDS_ATTENTION in app._headline_label.text()
    assert not app._alert_frame.isHidden()
    assert "eșuează repetat" in app._alert_label.text()


def test_tray_app_archived_count_in_menu(qtbot: object, tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        from anaf_sync.state import CatalogEntry

        archive.record(
            CatalogEntry(
                message_id="m1",
                cif="111",
                direction="received",
                base_path="p",
                artifacts=["zip"],
            )
        )
    app = _app(tmp_path)
    app.refresh()
    assert "1" in app._archived_action.text()
    assert app._archived_action.text().startswith(MENU_ARCHIVED_INVOICES)


def test_tray_app_folder_disabled_when_config_missing(
    qtbot: object, tmp_path: Path
) -> None:
    app = TrayApp(
        state_path=tmp_path / "state.db", config_path=tmp_path / "absent.toml"
    )
    app.refresh()
    assert not app._folder_action.isEnabled()


def test_menu_header_grows_when_the_subline_starts_wrapping(
    qtbot: object, tmp_path: Path
) -> None:
    """Regression: the header kept the height measured for a shorter subline.

    `QMenu` caches action rects and never sees a label's text change, so a
    subline that grew to two lines ("acum 19 minute · 10 facturi noi") was
    painted into the one-line rect measured at the first popup — clipped, and
    overlapping the headline. Reopening the menu did not heal it, which is why
    it looked intermittent rather than broken.
    """
    app = _app(tmp_path)
    subline = app._subline_label
    header = app._header_action.defaultWidget()

    app._apply(_status(subline="Niciodată"))
    app._menu.popup(app._menu.pos())
    QApplication.processEvents()
    assert subline.height() >= subline.heightForWidth(subline.width())

    app._apply(_status(subline="Ultima sincronizare: acum 19 minute · 10 facturi noi"))
    QApplication.processEvents()
    assert subline.text().endswith("facturi noi")  # the long text really landed
    assert subline.height() >= subline.heightForWidth(subline.width())
    assert header.height() >= header.sizeHint().height()

    app._menu.close()


def test_tray_app_sync_item_toggles_while_running(
    qtbot: object, tmp_path: Path
) -> None:
    app = _app(tmp_path)
    app.refresh()
    app._on_sync_started()
    assert app._sync_action.text() == MENU_SYNCING
    assert not app._sync_action.isEnabled()
    app._on_sync_finished(0)
    assert app._sync_action.text() == MENU_SYNC_NOW
    assert app._sync_action.isEnabled()


# -- watcher ------------------------------------------------------------------


def test_watcher_debounces_events_into_one_change(
    qtbot: object, tmp_path: Path
) -> None:
    Archive.open(tmp_path / "state.db").close()
    watcher = StateWatcher(tmp_path / "state.db")
    watcher.start()
    with qtbot.waitSignal(watcher.changed, timeout=3000):
        watcher._on_event(str(tmp_path / "state.db"))
    watcher.stop()


def test_watcher_poll_emits_change(qtbot: object, tmp_path: Path) -> None:
    Archive.open(tmp_path / "state.db").close()
    watcher = StateWatcher(tmp_path / "state.db")
    with qtbot.waitSignal(watcher.changed, timeout=1000):
        watcher._on_poll()


# -- runner -------------------------------------------------------------------


def test_sync_runner_starts_not_running() -> None:
    runner = SyncRunner()
    assert runner.running is False


def test_sync_runner_finished_clears_and_reports(qtbot: object) -> None:
    runner = SyncRunner()
    runner._process = object()  # pretend a child is live
    with qtbot.waitSignal(runner.finished, timeout=1000) as blocker:
        runner._on_finished(0, None)
    assert blocker.args == [0]
    assert runner.running is False


# -- the tray owns two independent windows ------------------------------------


def test_settings_opens_its_own_window_without_the_catalog(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anaf_sync.tray import settings_view as sv
    from anaf_sync.tray.settings_window import SettingsWindow

    monkeypatch.setattr(sv, "schedule_status", lambda: "not installed")
    app = _app(tmp_path)
    app._open_settings()

    assert isinstance(app._settings, SettingsWindow)
    assert app._settings.isVisible()
    # Setări no longer drags the catalog open behind it — they are unrelated.
    assert app._window is None
    app._settings.close()


def test_catalog_settings_button_reaches_the_tray(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anaf_sync.tray import settings_view as sv

    monkeypatch.setattr(sv, "schedule_status", lambda: "not installed")
    app = _app(tmp_path)
    app._open_window()
    assert app._window is not None

    app._window._settings_button.click()
    assert app._settings is not None and app._settings.isVisible()
    app._settings.close()
    app._window.close()


def test_saving_settings_refreshes_the_tray(
    qtbot: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from anaf_sync.tray import settings_view as sv

    monkeypatch.setattr(sv, "schedule_status", lambda: "not installed")
    app = _app(tmp_path)
    calls: list[int] = []
    # Patch before the window exists: `saved` binds whatever `refresh` names
    # at connect time, so patching afterwards would connect the original.
    monkeypatch.setattr(app, "refresh", lambda: calls.append(1))

    app._open_settings()
    assert app._settings is not None
    app._settings._view._lookback.setValue(30)
    app._settings._view._save()

    assert calls  # the tray status re-reads config.toml straight away
