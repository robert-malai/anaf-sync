"""The Setări form — every control maps to an existing ``SyncConfig`` key.

A scrollable three-section form (Companie / Arhivă / Programare) with a pinned
save bar. It reads the current config, offers the union of configured and
seen-in-archive CIFs as the follow list (anafpy has no authorized-CIF API),
previews the path template live, and on save writes a minimal diff through
:mod:`config_io`. Copy is in :mod:`strings`, colours in :mod:`theme`; the form
never syncs or writes anything but ``config.toml``.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import SyncConfig, load_config
from ..scheduling import ScheduleError
from ..scheduling import install as schedule_install
from ..scheduling import status as schedule_status
from ..state import Archive
from . import config_io, strings
from .preview import render_preview
from .theme import LIGHT, MONO_FONT_FAMILY, Theme

__all__ = ["SettingsView"]

_ARTIFACTS = ("zip", "pdf", "xml", "signature", "metadata")
# (combo label, scheduling kwarg, value) — maps to scheduling.py's presets.
_FREQUENCIES = (
    (strings.FREQ_1H, "every", "1h"),
    (strings.FREQ_3H, "every", "3h"),
    (strings.FREQ_6H, "every", "6h"),
    (strings.FREQ_12H, "every", "12h"),
    (strings.FREQ_DAILY, "daily", "06:00"),
)
_DEFAULT_FREQ = 2  # 6 ore


class SettingsView(QWidget):
    """The config editor; emits :attr:`saved` after a successful write."""

    saved = Signal()

    def __init__(
        self,
        *,
        state_path: Path,
        config_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state_path = state_path
        self._config_path = config_path
        self._theme: Theme = LIGHT
        self._cif_buttons: dict[str, QToolButton] = {}
        self._artifact_boxes: dict[str, QCheckBox] = {}

        self._config = self._load_config()
        self._build()

    # -- data -----------------------------------------------------------------

    def _load_config(self) -> SyncConfig | None:
        try:
            return load_config(self._config_path)
        except (FileNotFoundError, ValueError):
            return None

    def _available_cifs(self) -> list[str]:
        cifs = set(self._config.cifs if self._config else [])
        if self._state_path.exists():
            with Archive.open_readonly(self._state_path) as archive:
                cifs.update(archive.distinct_cifs())
        return sorted(cifs)

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if self._config is None:
            label = QLabel(strings.SETTINGS_NEEDS_INIT)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addWidget(label)
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        form = QWidget()
        self._form_layout = QVBoxLayout(form)
        self._form_layout.setContentsMargins(24, 20, 24, 20)
        self._form_layout.setSpacing(24)
        self._form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._build_company()
        self._build_archive()
        self._build_schedule()
        scroll.setWidget(form)
        outer.addWidget(scroll, 1)
        outer.addWidget(self._build_save_bar())

        self._refresh_preview()
        self._update_save_enabled()

    def _section(self, title: str) -> None:
        header = QLabel(title.upper())
        header.setObjectName("sectionHeader")
        self._form_layout.addWidget(header)

    def _build_company(self) -> None:
        assert self._config is not None  # _build guards the None case
        self._section(strings.SET_COMPANY)

        chips = QWidget()
        chip_layout = QHBoxLayout(chips)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(6)
        selected = set(self._config.cifs) if self._config else set()
        for cif in self._available_cifs():
            button = QToolButton()
            button.setText(cif)
            button.setCheckable(True)
            button.setChecked(cif in selected)
            button.clicked.connect(self._on_cif_toggled)
            self._cif_buttons[cif] = button
            chip_layout.addWidget(button)
        self._add_cif_edit = QLineEdit()
        self._add_cif_edit.setPlaceholderText(strings.ADD_CIF_PLACEHOLDER)
        self._add_cif_edit.setFixedWidth(90)
        add_button = QPushButton(strings.BTN_ADD_CIF)
        add_button.clicked.connect(self._on_add_cif)
        chip_layout.addWidget(self._add_cif_edit)
        chip_layout.addWidget(add_button)
        chip_layout.addStretch(1)
        self._chip_row = chip_layout
        self._labeled(strings.SET_CIFS, chips, strings.HELP_CIFS)

        directions = QWidget()
        dir_layout = QHBoxLayout(directions)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        self._direction_group = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        current_dir = self._config.direction.value if self._config else "received"
        for value, label in (
            ("received", strings.DIR_RECEIVED),
            ("sent", strings.DIR_SENT),
            ("both", strings.DIR_BOTH),
        ):
            radio = QRadioButton(label)
            radio.setChecked(value == current_dir)
            self._direction_group.addButton(radio)
            self._radios[value] = radio
            dir_layout.addWidget(radio)
        dir_layout.addStretch(1)
        self._labeled(strings.SET_DIRECTION, directions)

        lookback = QWidget()
        lb_layout = QHBoxLayout(lookback)
        lb_layout.setContentsMargins(0, 0, 0, 0)
        self._lookback = QSlider(Qt.Orientation.Horizontal)
        self._lookback.setMinimum(1)
        self._lookback.setMaximum(60)
        self._lookback.setValue(self._config.lookback_days if self._config else 60)
        self._lookback_label = QLabel()
        self._lookback.valueChanged.connect(self._on_lookback)
        lb_layout.addWidget(self._lookback, 1)
        lb_layout.addWidget(self._lookback_label)
        self._on_lookback(self._lookback.value())
        self._labeled(strings.SET_LOOKBACK, lookback, strings.HELP_LOOKBACK)

    def _build_archive(self) -> None:
        assert self._config is not None  # _build guards the None case
        self._section(strings.SET_ARCHIVE)

        directory = QWidget()
        dir_layout = QHBoxLayout(directory)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        self._directory = QLineEdit(str(self._config.output.directory))
        self._directory.setReadOnly(True)
        self._directory.setProperty("mono", True)
        choose = QPushButton(strings.BTN_CHOOSE)
        choose.clicked.connect(self._on_choose_dir)
        dir_layout.addWidget(self._directory, 1)
        dir_layout.addWidget(choose)
        self._labeled(strings.SET_DIR, directory)

        template_box = QWidget()
        tb_layout = QVBoxLayout(template_box)
        tb_layout.setContentsMargins(0, 0, 0, 0)
        tb_layout.setSpacing(6)
        self._template = QLineEdit(self._config.output.template)
        self._template.setProperty("mono", True)
        self._template.textChanged.connect(self._refresh_preview)
        self._preview = QLabel()
        self._preview.setObjectName("preview")
        self._preview.setWordWrap(True)
        tb_layout.addWidget(self._template)
        tb_layout.addWidget(self._preview)
        self._labeled(strings.SET_TEMPLATE, template_box)

        cards = QWidget()
        grid = QGridLayout(cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        configured = {a.value for a in self._config.output.artifacts}
        for i, name in enumerate(_ARTIFACTS):
            box = QCheckBox()
            box.setChecked(name in configured)
            box.clicked.connect(self._update_save_enabled)
            self._artifact_boxes[name] = box
            grid.addWidget(self._artifact_card(name, box), i // 3, i % 3)
        self._labeled(strings.SET_ARTIFACTS, cards)

    def _build_schedule(self) -> None:
        self._section(strings.SET_SCHEDULE)
        self._frequency = QComboBox()
        for label, _kind, _value in _FREQUENCIES:
            self._frequency.addItem(label)
        self._frequency.setCurrentIndex(_DEFAULT_FREQ)
        self._labeled(strings.SET_FREQUENCY, self._frequency)

        status = schedule_status()
        active = status != "not installed"
        self._schedule_status = QLabel(
            strings.SCHEDULE_ACTIVE if active else strings.SCHEDULE_INACTIVE
        )
        self._schedule_status.setObjectName(
            "scheduleActive" if active else "scheduleInactive"
        )
        self._form_layout.addWidget(self._schedule_status)

    def _build_save_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("saveBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 10, 24, 10)
        note = QLabel(strings.SAVE_NOTE)
        note.setObjectName("saveNote")
        layout.addWidget(note)
        layout.addStretch(1)
        cancel = QPushButton(strings.BTN_CANCEL)
        cancel.clicked.connect(self._reset)
        self._save_button = QPushButton(strings.BTN_SAVE)
        self._save_button.setObjectName("savePrimary")
        self._save_button.clicked.connect(self._save)
        layout.addWidget(cancel)
        layout.addWidget(self._save_button)
        return bar

    # -- small builders -------------------------------------------------------

    def _labeled(self, label: str, control: QWidget, help_text: str = "") -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        key = QLabel(label)
        key.setFixedWidth(150)
        key.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(key)

        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(4)
        holder_layout.addWidget(control)
        if help_text:
            hint = QLabel(help_text)
            hint.setObjectName("help")
            hint.setWordWrap(True)
            holder_layout.addWidget(hint)
        layout.addWidget(holder, 1)
        self._form_layout.addWidget(row)

    def _artifact_card(self, name: str, box: QCheckBox) -> QWidget:
        card = QFrame()
        card.setObjectName("artifactCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(box)
        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        title = QLabel(name)
        title.setProperty("mono", True)
        desc = QLabel(strings.ARTIFACT_DESCRIPTIONS[name])
        desc.setObjectName("help")
        desc.setWordWrap(True)
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        layout.addWidget(text, 1)
        return card

    # -- interactions ---------------------------------------------------------

    def _on_cif_toggled(self) -> None:
        if not self._selected_cifs():
            # The last checked CIF refuses to uncheck (config needs min 1).
            sender = self.sender()
            if isinstance(sender, QToolButton):
                sender.setChecked(True)
        self._update_save_enabled()

    def _on_add_cif(self) -> None:
        cif = self._add_cif_edit.text().strip().upper().removeprefix("RO")
        if not cif.isdigit() or cif in self._cif_buttons:
            return
        button = QToolButton()
        button.setText(cif)
        button.setCheckable(True)
        button.setChecked(True)
        button.clicked.connect(self._on_cif_toggled)
        self._cif_buttons[cif] = button
        self._chip_row.insertWidget(self._chip_row.count() - 3, button)
        self._add_cif_edit.clear()
        self._update_save_enabled()

    def _on_lookback(self, value: int) -> None:
        self._lookback_label.setText(strings.lookback_value(value))

    def _on_choose_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, strings.SET_DIR)
        if chosen:
            self._directory.setText(chosen)

    def _refresh_preview(self) -> None:
        result = render_preview(self._template.text(), directory=self._directory.text())
        if result.ok:
            self._preview.setText(strings.PREVIEW_PREFIX + result.text)
            self._preview.setProperty("state", "ok")
        else:
            self._preview.setText(result.text)
            self._preview.setProperty("state", "err")
        self._restyle(self._preview)
        self._update_save_enabled()

    def _update_save_enabled(self) -> None:
        ok = (
            self._preview.property("state") == "ok"
            and bool(self._selected_artifacts())
            and bool(self._selected_cifs())
        )
        self._save_button.setEnabled(ok)

    # -- read form state ------------------------------------------------------

    def _selected_cifs(self) -> list[str]:
        return [cif for cif, btn in self._cif_buttons.items() if btn.isChecked()]

    def _selected_artifacts(self) -> list[str]:
        return [name for name in _ARTIFACTS if self._artifact_boxes[name].isChecked()]

    def _selected_direction(self) -> str:
        for value, radio in self._radios.items():
            if radio.isChecked():
                return value
        return "received"

    def _form(self) -> config_io.SettingsForm:
        return config_io.SettingsForm(
            cifs=self._selected_cifs(),
            direction=self._selected_direction(),
            lookback_days=self._lookback.value(),
            directory=self._directory.text(),
            template=self._template.text(),
            artifacts=self._selected_artifacts(),
        )

    # -- save / reset ---------------------------------------------------------

    def _save(self) -> None:
        doc = config_io.load(self._config_path)
        config_io.apply(doc, self._form())
        try:
            config_io.validate(doc)
        except ValidationError:
            self._preview.setText(strings.SETTINGS_NEEDS_INIT)
            return
        config_io.save(doc, self._config_path)
        self._reinstall_schedule_if_active()
        self._config = self._load_config()  # reload from disk, no cached copy
        self.saved.emit()

    def _reinstall_schedule_if_active(self) -> None:
        if schedule_status() == "not installed":
            return  # removing a schedule stays a CLI concern this milestone
        _label, kind, value = _FREQUENCIES[self._frequency.currentIndex()]
        try:
            if kind == "every":
                schedule_install(every=value, daily_at=None)
            else:
                schedule_install(every=None, daily_at=value)
        except ScheduleError:
            pass  # a failed re-schedule must not lose the saved config

    def _reset(self) -> None:
        # Rebuild the form from the on-disk config, discarding edits.
        layout = self.layout()
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
        self._cif_buttons.clear()
        self._artifact_boxes.clear()
        self._config = self._load_config()
        self._build()

    # -- theming --------------------------------------------------------------

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(_settings_qss(theme))

    def _restyle(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def _settings_qss(theme: Theme) -> str:
    return f"""
QWidget {{ background-color:{theme.window_bg}; color:{theme.text}; }}
#sectionHeader {{ color:{theme.faint}; font-size:11px; letter-spacing:1px; }}
#help {{ color:{theme.faint}; font-size:11px; }}
QLineEdit[mono="true"], QLabel[mono="true"] {{
    font-family:{MONO_FONT_FAMILY}; }}
QLineEdit {{ background-color:{theme.panel_bg}; color:{theme.text};
    border:1px solid {theme.border}; border-radius:6px; padding:5px 8px; }}
#preview[state="ok"] {{ background-color:{theme.green_bg}; color:{theme.green};
    border-radius:6px; padding:6px; font-family:{MONO_FONT_FAMILY};
    font-size:11px; }}
#preview[state="err"] {{ background-color:{theme.red_bg}; color:{theme.red};
    border-radius:6px; padding:6px; font-family:{MONO_FONT_FAMILY};
    font-size:11px; }}
QToolButton {{ background-color:{theme.window_bg}; color:{theme.muted};
    border:1px solid {theme.border}; border-radius:6px; padding:4px 8px;
    font-family:{MONO_FONT_FAMILY}; }}
QToolButton:checked {{ background-color:{theme.accent_soft_bg};
    color:{theme.accent}; border-color:{theme.accent}; }}
#artifactCard {{ border:1px solid {theme.border}; border-radius:6px; }}
#saveBar {{ background-color:{theme.window_bg};
    border-top:1px solid {theme.border}; }}
#saveNote {{ color:{theme.faint}; font-size:11px; }}
#savePrimary {{ background-color:{theme.accent}; color:{theme.on_accent};
    border:none; border-radius:6px; padding:6px 12px; }}
#savePrimary:disabled {{ background-color:{theme.border};
    color:{theme.faint}; }}
#scheduleActive {{ color:{theme.green}; font-size:12.5px; }}
#scheduleInactive {{ color:{theme.muted}; font-size:12.5px; }}
"""
