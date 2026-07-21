"""The variable reference panel: no drift, real renders, caret insertion."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea  # noqa: E402

from anaf_sync.config import write_default_config  # noqa: E402
from anaf_sync.tray import settings_view as sv  # noqa: E402
from anaf_sync.tray import store  # noqa: E402
from anaf_sync.tray.flowgrid import WIDE_BREAKPOINT, group_column_count  # noqa: E402
from anaf_sync.tray.preview import sample_context  # noqa: E402
from anaf_sync.tray.settings_view import SettingsView  # noqa: E402
from anaf_sync.tray.template_help import (  # noqa: E402
    _SETTINGS_KEY,
    GROUPS,
    SPECIFIERS,
    TemplateHelp,
    render_sample,
    rendered_samples,
    variable_count,
)

#: The field column at the 760px minimum window: 760 less the form's 24px
#: margins, the 150px label column and its 12px gutter, less the form scroll
#: area's own bar. The panel must fit inside it or the form scrolls sideways.
_FIELD_COLUMN_AT_MINIMUM = 760 - 48 - 150 - 12 - 16


@pytest.fixture()
def _no_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sv, "schedule_status", lambda: "not installed")


def _forget_disclosure_state() -> None:
    """Drop the remembered expanded/collapsed flag, flushing it to disk.

    Explicit `sync()`: the panel reads through a *fresh* `QSettings` on every
    construction, so an unflushed removal would leave the previous test's state
    in place and make these assertions order-dependent.

    Resolved through the module, never `from ... import geometry_settings`:
    conftest redirects the store by rebinding that module attribute, and an
    import-time binding would keep pointing at the developer's real settings.
    """
    settings = store.geometry_settings()
    settings.remove(_SETTINGS_KEY)
    settings.sync()


def _rows(panel: TemplateHelp) -> list[QFrame]:
    return [w for w in panel.findChildren(QFrame) if w.objectName() == "helpRow"]


def _names() -> set[str]:
    return {v.name for group in GROUPS for v in group.variables}


def test_panel_documents_exactly_the_template_context() -> None:
    """The anti-drift gate.

    A variable added to `context.build_context` (and so to `sample_context`)
    without a row here fails *this* test rather than silently producing another
    stale list — which is how the handoff's own list went stale in the first
    place.
    """
    assert _names() == set(sample_context())


def test_every_variable_has_a_rendered_sample() -> None:
    samples = rendered_samples()
    assert set(samples) == _names()
    assert all(value for value in samples.values())


def test_samples_come_from_the_real_renderer() -> None:
    # Not literals: the sanitiser rewrites ":" (illegal in a Windows path) and
    # strips the trailing dot of "S.R.L.". If either ever stops being true the
    # legend must change with it, not keep quoting the old output.
    samples = rendered_samples()
    assert samples["created"] == "2026-07-06 09-30-00"
    assert samples["partner_name"] == "ACME CONSTRUCT S.R.L"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("{issue_date:%Y}", "2026"),
        ("{issue_date:%m}", "07"),
        ("{issue_date:%Y-%m-%d}", "2026-07-03"),
        ("{created:%H%M}", "0930"),
        ("{issue_month!u}", "IULIE"),
        ("{issue_month!c}", "Iulie"),
        ("{issue_month!l}", "iulie"),
        ("{partner_name!t}", "Acme Construct S.R.L"),
    ],
)
def test_specifier_examples_render_as_advertised(
    expression: str, expected: str
) -> None:
    assert render_sample(expression) == expected


def test_every_documented_specifier_is_covered_above() -> None:
    documented = {e for _title, expressions in SPECIFIERS for e in expressions}
    assert documented == {
        "{issue_date:%Y}",
        "{issue_date:%m}",
        "{issue_date:%Y-%m-%d}",
        "{created:%H%M}",
        "{issue_month!u}",
        "{issue_month!c}",
        "{issue_month!l}",
        "{partner_name!t}",
    }


def test_group_columns_share_the_artifact_breakpoint() -> None:
    # One reflow moment for the whole form, not two competing ones.
    assert group_column_count(WIDE_BREAKPOINT) == 3
    assert group_column_count(WIDE_BREAKPOINT - 1) == 1


def test_expanding_never_makes_the_form_scroll_sideways(
    _no_schedule: None, qtbot: object, tmp_path: Path
) -> None:
    """Regression: the specifier strip used to be an unwrappable row.

    Four example chips side by side are ~1040px wide, so at the 760px minimum
    window the panel's minimum width was 612 and the *whole Setări form* — path
    field, artifact cards and all — grew a horizontal scrollbar the moment the
    user expanded the reference.
    """
    config = tmp_path / "config.toml"
    write_default_config(config)
    view = SettingsView(state_path=tmp_path / "state.db", config_path=config)
    view._template_help._toggle.setChecked(True)
    view.resize(760, 620)
    view.show()

    scroll = view.findChild(QScrollArea)
    assert scroll is not None
    assert view._template_help.minimumSizeHint().width() <= _FIELD_COLUMN_AT_MINIMUM
    assert scroll.horizontalScrollBar().maximum() == 0


def test_the_list_scrolls_only_below_the_reflow_breakpoint(
    _no_schedule: None, qtbot: object, tmp_path: Path
) -> None:
    """Regression: the list scrolled at *every* width.

    `QScrollArea` sizes a resizable child from `sizeHint`, never from
    `heightForWidth`, and a re-flowing layout reports its tallest (1-column)
    arrangement there — so the 3-column layout was laid out correctly and still
    scrolled, defeating the whole point of going wide.
    """
    config = tmp_path / "config.toml"
    write_default_config(config)

    def scrolls(width: int) -> bool:
        view = SettingsView(state_path=tmp_path / "state.db", config_path=config)
        view._template_help._toggle.setChecked(True)
        view.resize(width, 780)
        view.show()
        QApplication.processEvents()
        inner = view._template_help.findChild(QScrollArea)
        assert inner is not None
        return inner.verticalScrollBar().maximum() > 0

    assert not scrolls(1200)  # 3-up: every variable visible at once
    assert scrolls(760)  # 1-up: 15 rows cannot fit, and should not try


def test_panel_defaults_to_collapsed(qtbot: object) -> None:
    """A reference most users open once must not cost 300px by default."""
    _forget_disclosure_state()
    panel = TemplateHelp()
    assert not panel._card.isVisibleTo(panel)

    panel._toggle.setChecked(True)
    assert panel._card.isVisibleTo(panel)

    panel._toggle.setChecked(False)
    assert not panel._card.isVisibleTo(panel)


def test_expanded_state_survives_a_reopen(qtbot: object) -> None:
    """The disclosure is remembered next to the window geometry.

    Reopening Setări re-reads `config.toml` and rebuilds the form, so without
    persistence a user who wants the reference open has to reopen it every
    single time.
    """
    _forget_disclosure_state()
    first = TemplateHelp()
    first._toggle.setChecked(True)

    reopened = TemplateHelp()
    assert reopened._card.isVisibleTo(reopened)

    reopened._toggle.setChecked(False)
    reopened_again = TemplateHelp()
    assert not reopened_again._card.isVisibleTo(reopened_again)


def test_clicking_a_variable_asks_for_its_token(qtbot: object) -> None:
    panel = TemplateHelp()
    emitted: list[str] = []
    panel.insert_requested.connect(emitted.append)

    QTest.mouseClick(_rows(panel)[0], Qt.MouseButton.LeftButton)
    assert emitted == ["{number}"]  # first row of the first group


def test_rows_are_keyboard_operable(qtbot: object) -> None:
    """Tab walks the list and Space inserts — the panel needs no mouse."""
    panel = TemplateHelp()
    emitted: list[str] = []
    panel.insert_requested.connect(emitted.append)
    row = _rows(panel)[0]
    assert row.focusPolicy() == Qt.FocusPolicy.StrongFocus

    QTest.keyClick(row, Qt.Key.Key_Space)
    QTest.keyClick(row, Qt.Key.Key_Return)
    assert emitted == ["{number}", "{number}"]


def test_specifier_chips_insert_the_whole_expression(qtbot: object) -> None:
    panel = TemplateHelp()
    emitted: list[str] = []
    panel.insert_requested.connect(emitted.append)

    # The chips follow the variable rows in construction order.
    QTest.mouseClick(_rows(panel)[variable_count()], Qt.MouseButton.LeftButton)
    assert emitted == ["{issue_date:%Y}"]


def test_insertion_splices_at_the_caret(
    _no_schedule: None, qtbot: object, tmp_path: Path
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config)
    view = SettingsView(state_path=tmp_path / "state.db", config_path=config)

    view._template.setText("{cif}/{number}")
    view._template.setCursorPosition(len("{cif}"))
    view._insert_into_template("/{issue_month}")

    assert view._template.text() == "{cif}/{issue_month}/{number}"
    # focusWidget, not hasFocus: the view is never shown here, so its window is
    # inactive and hasFocus() is False even though focus did move to the field.
    assert view.focusWidget() is view._template


def test_insertion_replaces_the_selection(
    _no_schedule: None, qtbot: object, tmp_path: Path
) -> None:
    config = tmp_path / "config.toml"
    write_default_config(config)
    view = SettingsView(state_path=tmp_path / "state.db", config_path=config)

    view._template.setText("{cif}/{number}")
    view._template.setSelection(len("{cif}/"), len("{number}"))
    view._insert_into_template("{partner_name}")

    assert view._template.text() == "{cif}/{partner_name}"


def test_the_count_in_the_header_matches_the_data() -> None:
    assert variable_count() == len(_names())
