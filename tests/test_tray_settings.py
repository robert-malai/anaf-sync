"""Qt-dependent Setări form: preview state, save-enable rules, save writes."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QPushButton  # noqa: E402

from anaf_sync.config import load_config, write_default_config  # noqa: E402
from anaf_sync.tray import settings_view as sv  # noqa: E402
from anaf_sync.tray import strings  # noqa: E402
from anaf_sync.tray.settings_view import SettingsView  # noqa: E402


@pytest.fixture(autouse=True)
def _no_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never touch the real OS scheduler from a test.
    monkeypatch.setattr(sv, "schedule_status", lambda: "not installed")


def _view(tmp_path: Path) -> SettingsView:
    config = tmp_path / "config.toml"
    write_default_config(config)
    return SettingsView(state_path=tmp_path / "state.db", config_path=config)


def _button(view: SettingsView, text: str) -> QPushButton:
    return next(
        button for button in view.findChildren(QPushButton) if button.text() == text
    )


def test_save_enabled_only_after_modification(qtbot: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    assert not view._save_button.isEnabled()

    view._lookback.setValue(30)
    assert view._save_button.isEnabled()

    view._lookback.setValue(60)
    assert not view._save_button.isEnabled()


@pytest.mark.parametrize(
    "field",
    ["cifs", "direction", "directory", "template", "artifacts", "frequency"],
)
def test_each_modification_enables_save(
    field: str, qtbot: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)

    if field == "cifs":
        view._add_cif_edit.setText("40118293")
        view._on_add_cif()
    elif field == "direction":
        view._radios["sent"].click()
    elif field == "directory":
        view._directory.setText(str(tmp_path / "archive"))
    elif field == "template":
        view._template.setText(view._template.text() + "-copy")
    elif field == "artifacts":
        view._artifact_boxes["xml"].click()
    else:
        view._frequency.setCurrentIndex(1)

    assert view._save_button.isEnabled()


def test_cancel_reloads_file_without_hiding_settings(
    qtbot: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)
    qtbot.addWidget(view)
    view.show()

    view._lookback.setValue(30)
    config = tmp_path / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "lookback_days = 60", "lookback_days = 45"
        ),
        encoding="utf-8",
    )

    qtbot.mouseClick(_button(view, strings.BTN_CANCEL), Qt.MouseButton.LeftButton)

    assert view.layout() is not None
    assert view.layout().count() == 2
    assert view._lookback.value() == 45
    qtbot.waitUntil(lambda: view._lookback.isVisible())
    assert view._lookback.isVisible()
    assert not view._save_button.isEnabled()


def test_save_disabled_on_template_error_and_reenabled_on_fix(
    qtbot: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)
    view._template.setText("{numer}")  # unknown variable → red preview
    assert view._preview.property("state") == "err"
    assert not view._save_button.isEnabled()

    view._template.setText("{cif}/{number}")
    assert view._preview.property("state") == "ok"
    assert view._save_button.isEnabled()


def test_save_disabled_when_no_artifacts_selected(
    qtbot: object, tmp_path: Path
) -> None:
    view = _view(tmp_path)
    for box in view._artifact_boxes.values():
        box.setChecked(False)
    view._update_save_enabled()
    assert not view._save_button.isEnabled()


def test_last_cif_cannot_be_removed(qtbot: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    view._cif_buttons["12345678"].click()  # try to remove the sole CIF
    assert view._selected_cifs() == ["12345678"]  # refused
    assert view._cif_error.text() == strings.CIF_LAST_REMAINS


def test_add_then_remove_cif(qtbot: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    view._add_cif_edit.setText("40118293")
    view._on_add_cif()
    assert view._selected_cifs() == ["12345678", "40118293"]  # config order kept
    view._cif_buttons["12345678"].click()  # no longer the last one
    assert view._selected_cifs() == ["40118293"]


def test_add_cif_validates_digits(qtbot: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    view._add_cif_edit.setText("RO40118293")
    view._on_add_cif()
    assert "40118293" in view._cif_buttons  # RO prefix stripped, digits kept
    assert view._cif_error.text() == ""  # cleared on success
    view._add_cif_edit.setText("not-a-cif")
    view._on_add_cif()
    assert "not-a-cif" not in view._cif_buttons
    assert view._cif_error.text() == strings.CIF_INVALID


def test_add_cif_rejects_duplicate(qtbot: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    view._add_cif_edit.setText("12345678")  # already configured
    view._on_add_cif()
    assert view._selected_cifs() == ["12345678"]
    assert view._cif_error.text() == strings.CIF_DUPLICATE


def test_save_writes_minimal_diff(qtbot: object, tmp_path: Path) -> None:
    view = _view(tmp_path)
    config = tmp_path / "config.toml"
    original = config.read_text(encoding="utf-8")

    view._lookback.setValue(30)
    view._save()

    updated = config.read_text(encoding="utf-8")
    assert updated == original.replace("lookback_days = 60", "lookback_days = 30")
    assert load_config(config).lookback_days == 30
    assert not view._save_button.isEnabled()
