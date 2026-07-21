"""The Setări form — every control maps to an existing ``SyncConfig`` key.

A scrollable three-section form (Companie / Arhivă / Programare) with a pinned
save bar. It reads the current config, takes the followed CUIs as free entry
(validated exactly as ``config.py`` does, at least one kept; archive-seen CUIs
are offered only as autocomplete — see DESIGN.md §8 for why ANAF's own
authorization inventory is not the source), previews the path template live,
and on save writes a minimal diff through
:mod:`config_io`. Colours are in :mod:`theme`; the form never syncs or writes
anything but ``config.toml``.

The view is hosted by :mod:`settings_window`, which owns the two exits: it
closes on :attr:`SettingsView.saved` and on :attr:`SettingsView.cancelled`.
Nothing outside the window depends on pending edits, so cancelling needs no
confirmation — :meth:`SettingsView.reload` re-reads ``config.toml`` and a
cancelled session leaves no residue (DESIGN.md §10).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QFileDialog,
    QFrame,
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
from . import config_io
from . import format as fmt
from .flowgrid import ArtifactGrid
from .preview import render_preview
from .template_help import TemplateHelp, template_help_qss
from .theme import LIGHT, MONO_FONT_FAMILY, Theme

__all__ = ["SettingsView"]

# Artifact cards: English name (mono, a code identifier) + Romanian description.
_ARTIFACTS = {
    "zip": "arhiva semnată originală",
    "pdf": "redarea oficială ANAF",
    "xml": "XML-ul UBL al facturii",
    "signature": "semnătura MF detașată",
    "metadata": "fișier JSON cu detaliile mesajului",
}
# (combo label, scheduling kwarg, value) — maps to scheduling.py's presets.
_FREQUENCIES = (
    ("La fiecare oră", "every", "1h"),
    ("La fiecare 3 ore", "every", "3h"),
    ("La fiecare 6 ore", "every", "6h"),
    ("La fiecare 12 ore", "every", "12h"),
    ("O dată pe zi", "daily", "06:00"),
)
_DEFAULT_FREQ = 2  # 6 ore

# The label column is fixed and the field column takes all the rest; the
# window itself carries the ceiling (DESIGN.md §10). Two controls opt out of
# stretching: a 1-60 slider longer than this reads as a progress bar, and help
# text is prose, which has a reading width no window size changes.
_LABEL_WIDTH = 150
_SLIDER_MAX_WIDTH = 480
_HELP_MAX_WIDTH = 620

#: Everything a row spends before the field column starts: the form's 24px
#: margins either side, the label column, its 12px gutter, and the scroll
#: area's own bar. Field column = form width less this.
_FORM_CHROME = 48 + _LABEL_WIDTH + 12 + 16

_DIR_LABEL = "Dosar arhivă"
_NEEDS_INIT = "Rulați `anaf-sync init` pentru a crea un config.toml."

# CIF validation messages (mirrored by tests).
CIF_INVALID = "CIF invalid — folosește doar cifre, fără prefixul RO."
CIF_DUPLICATE = "CIF-ul este deja în listă."
CIF_LAST_REMAINS = "Cel puțin un CIF trebuie să rămână în listă."


class SettingsView(QWidget):
    """The config editor; emits :attr:`saved` after a successful write."""

    #: Written to ``config.toml``; the window closes on it.
    saved = Signal()
    #: "Renunță" — discard and close, without touching ``config.toml``.
    cancelled = Signal()

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
        self._artifact_cards: dict[str, QFrame] = {}
        self._baseline_form: config_io.SettingsForm | None = None
        self._baseline_frequency = _DEFAULT_FREQ

        self._config = self._load_config()
        self._build()

    # -- data -----------------------------------------------------------------

    def _load_config(self) -> SyncConfig | None:
        try:
            return load_config(self._config_path)
        except (FileNotFoundError, ValueError):
            return None

    def _suggested_cifs(self) -> list[str]:
        """CIFs already seen in the archive — autocomplete only, never a gate."""
        if not self._state_path.exists():
            return []
        with Archive.open_readonly(self._state_path) as archive:
            return sorted(archive.distinct_cifs())

    def minimum_width_hint(self) -> int:
        """The narrowest this form can be drawn without scrolling sideways.

        The variable reference is the one thing here that cannot shrink — its
        specifier chips are fixed-size rendered text — and its width is a
        function of the platform's mono font, not of any number we can pick.
        Windows draws the same chips wider than Linux does, which is how the
        form ended up with a horizontal scrollbar at the 760px minimum the
        window used to hard-code (#1). So the window asks the form, and the
        form asks the panel.

        Two independent floors, whichever is larger:

        * the reference panel plus the form chrome. The panel lives *inside* the
          scroll area, so it never reaches this widget's own minimum — it just
          overflows and produces the horizontal scrollbar #1 is about.
        * this widget's layout minimum, which is dominated by the save bar. That
          bar sits *outside* the scroll area, so its note and two buttons set a
          hard floor no scrolling can absorb.

        Counting only the first is why an earlier attempt still asked for widths
        the form silently clamped up.

        Returns 0 when the config could not be read: that branch shows a single
        centred label and has no form to measure.
        """
        help_panel: TemplateHelp | None = getattr(self, "_template_help", None)
        if help_panel is None:
            return 0
        return max(
            help_panel.minimum_content_width() + _FORM_CHROME,
            self.minimumSizeHint().width(),
        )

    # -- construction ---------------------------------------------------------

    def _build(self) -> None:
        existing_layout = self.layout()
        if existing_layout is None:
            outer = QVBoxLayout(self)
        else:
            assert isinstance(existing_layout, QVBoxLayout)
            outer = existing_layout
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        if self._config is None:
            label = QLabel(_NEEDS_INIT)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addWidget(label)
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Fields stretch with the window; the scroll area absorbs the extra
        # height and its scrollbar disappears once the form fits (DESIGN.md §10).
        scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
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

        self._baseline_form = self._form()
        self._baseline_frequency = self._frequency.currentIndex()
        self._refresh_preview()

    def _section(self, title: str) -> None:
        if self._form_layout.count():  # a 1px rule between sections, not before
            rule = QFrame()
            rule.setObjectName("sectionRule")
            rule.setFrameShape(QFrame.Shape.HLine)
            rule.setFixedHeight(1)
            self._form_layout.addWidget(rule)
        header = QLabel(title.upper())
        header.setObjectName("sectionHeader")
        self._form_layout.addWidget(header)

    def _build_company(self) -> None:
        assert self._config is not None  # _build guards the None case
        self._section("Companie")

        chips = QWidget()
        chip_layout = QHBoxLayout(chips)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(6)
        self._chip_row = chip_layout
        self._add_cif_edit = QLineEdit()
        self._add_cif_edit.setPlaceholderText("CIF nou")
        self._add_cif_edit.setFixedWidth(90)
        # Archive-seen CIFs are a convenience over the catalog, not the source of
        # the list — the user may type any CIF the config would accept.
        if suggestions := self._suggested_cifs():
            self._add_cif_edit.setCompleter(QCompleter(suggestions, self))
        self._add_cif_edit.returnPressed.connect(self._on_add_cif)
        add_button = QPushButton("+ Adaugă CIF")
        add_button.clicked.connect(self._on_add_cif)
        chip_layout.addWidget(self._add_cif_edit)
        chip_layout.addWidget(add_button)
        chip_layout.addStretch(1)
        # Config order is the user's order; keep it rather than sorting.
        for cif in self._config.cifs:
            self._add_chip(cif)
        self._cif_error = QLabel()
        self._cif_error.setObjectName("cifError")
        self._cif_error.hide()

        entry = QWidget()
        entry_layout = QVBoxLayout(entry)
        entry_layout.setContentsMargins(0, 0, 0, 0)
        entry_layout.setSpacing(4)
        entry_layout.addWidget(chips)
        entry_layout.addWidget(self._cif_error)
        self._labeled(
            "CIF-uri urmărite",
            entry,
            "CIF-urile companiilor pentru care se arhivează facturile — doar "
            "cifre, fără prefixul RO. Cel puțin unul rămâne în listă.",
        )

        directions = QWidget()
        dir_layout = QHBoxLayout(directions)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        self._direction_group = QButtonGroup(self)
        self._radios: dict[str, QRadioButton] = {}
        current_dir = self._config.direction.value if self._config else "received"
        for value, label in (
            ("received", "Primite"),
            ("sent", "Trimise"),
            ("both", "Ambele"),
        ):
            radio = QRadioButton(label)
            radio.setChecked(value == current_dir)
            radio.clicked.connect(self._update_save_enabled)
            self._direction_group.addButton(radio)
            self._radios[value] = radio
            dir_layout.addWidget(radio)
        dir_layout.addStretch(1)
        self._labeled("Direcție", directions)

        lookback = QWidget()
        lookback.setMaximumWidth(_SLIDER_MAX_WIDTH)
        lb_layout = QHBoxLayout(lookback)
        lb_layout.setContentsMargins(0, 0, 0, 0)
        self._lookback = QSlider(Qt.Orientation.Horizontal)
        self._lookback.setMinimum(1)
        self._lookback.setMaximum(60)
        self._lookback.setValue(self._config.lookback_days if self._config else 60)
        self._lookback_label = QLabel()
        self._lookback.valueChanged.connect(self._on_lookback)
        self._lookback.valueChanged.connect(self._update_save_enabled)
        lb_layout.addWidget(self._lookback, 1)
        lb_layout.addWidget(self._lookback_label)
        self._on_lookback(self._lookback.value())
        self._labeled(
            "Fereastră de căutare",
            lookback,
            "ANAF păstrează mesajele cel mult 60 de zile.",
        )

    def _build_archive(self) -> None:
        assert self._config is not None  # _build guards the None case
        self._section("Arhivă")

        directory = QWidget()
        dir_layout = QHBoxLayout(directory)
        dir_layout.setContentsMargins(0, 0, 0, 0)
        # as_posix, not str: the field shows the same forward-slash form the
        # file dialog and config_io.SettingsForm use, so the displayed path
        # matches what a save would write on Windows too.
        self._directory = QLineEdit(self._config.output.directory.as_posix())
        self._directory.setReadOnly(True)
        self._directory.setProperty("mono", True)
        self._directory.textChanged.connect(self._update_save_enabled)
        choose = QPushButton("Alege…")
        choose.clicked.connect(self._on_choose_dir)
        dir_layout.addWidget(self._directory, 1)
        dir_layout.addWidget(choose)
        self._labeled(_DIR_LABEL, directory)

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
        # Field → preview → reference. The preview is the primary feedback loop
        # and stays adjacent to the field; the legend is secondary (handoff §3).
        self._template_help = TemplateHelp()
        self._template_help.insert_requested.connect(self._insert_into_template)
        tb_layout.addWidget(self._template)
        tb_layout.addWidget(self._preview)
        tb_layout.addWidget(self._template_help)
        self._labeled("Șablon de denumire", template_box)

        cards = QWidget()
        grid = ArtifactGrid(cards)
        configured = {a.value for a in self._config.output.artifacts}
        for name in _ARTIFACTS:
            box = QCheckBox()
            box.setChecked(name in configured)
            box.clicked.connect(self._update_save_enabled)
            box.clicked.connect(lambda _checked, n=name: self._sync_card(n))
            self._artifact_boxes[name] = box
            grid.addWidget(self._artifact_card(name, box))
        self._labeled("Fișiere salvate", cards)

    def _build_schedule(self) -> None:
        self._section("Programare")
        self._frequency = QComboBox()
        for label, _kind, _value in _FREQUENCIES:
            self._frequency.addItem(label)
        self._frequency.setCurrentIndex(_DEFAULT_FREQ)
        self._frequency.currentIndexChanged.connect(self._update_save_enabled)
        # A select is sized by its content, not by the window (DESIGN.md §10).
        freq_row = QWidget()
        freq_layout = QHBoxLayout(freq_row)
        freq_layout.setContentsMargins(0, 0, 0, 0)
        freq_layout.addWidget(self._frequency)
        freq_layout.addStretch(1)
        self._labeled("Frecvență", freq_row)

        status = schedule_status()
        active = status != "not installed"
        self._schedule_status = QLabel("Activă" if active else "Dezactivată")
        self._schedule_status.setObjectName(
            "scheduleActive" if active else "scheduleInactive"
        )
        self._labeled("", self._schedule_status)

    def _build_save_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("saveBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 10, 24, 10)
        note = QLabel(
            "Modificările se scriu în config.toml — fișierul rămâne editabil manual"
        )
        note.setObjectName("saveNote")
        layout.addWidget(note)
        layout.addStretch(1)
        cancel = QPushButton("Renunță")
        cancel.setToolTip("Închide fereastra fără să salveze (Esc)")
        cancel.clicked.connect(self.cancelled)
        self._save_button = QPushButton("Salvează modificările")
        self._save_button.setObjectName("savePrimary")
        self._save_button.setToolTip("Scrie config.toml și închide fereastra")
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
        key.setFixedWidth(_LABEL_WIDTH)
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
            hint.setMaximumWidth(_HELP_MAX_WIDTH)
            holder_layout.addWidget(hint)
        layout.addWidget(holder, 1)
        self._form_layout.addWidget(row)

    def _sync_card(self, name: str) -> None:
        """Repaint a card for its checkbox — checked reads accent, per the mockup."""
        card = self._artifact_cards[name]
        card.setProperty("checked", self._artifact_boxes[name].isChecked())
        self._restyle(card)

    def _artifact_card(self, name: str, box: QCheckBox) -> QWidget:
        card = QFrame()
        card.setObjectName("artifactCard")
        card.setProperty("checked", box.isChecked())
        self._artifact_cards[name] = card
        layout = QHBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.addWidget(box)
        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        title = QLabel(name)
        title.setProperty("mono", True)
        desc = QLabel(_ARTIFACTS[name])
        desc.setObjectName("help")
        desc.setWordWrap(True)
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        layout.addWidget(text, 1)
        return card

    # -- interactions ---------------------------------------------------------

    def _add_chip(self, cif: str) -> None:
        """Append a followed-CIF chip; its × removes it from the list."""
        button = QToolButton()
        button.setObjectName("cifChip")
        button.setText(f"{cif}  ×")
        button.setToolTip(f"Elimină {cif}")
        button.clicked.connect(lambda: self._on_remove_cif(cif))
        self._cif_buttons[cif] = button
        # Keep the chips ahead of the entry field, button and stretch.
        self._chip_row.insertWidget(self._chip_row.count() - 3, button)

    def _cif_message(self, text: str) -> None:
        self._cif_error.setText(text)
        self._cif_error.setVisible(bool(text))

    def _on_remove_cif(self, cif: str) -> None:
        if len(self._cif_buttons) == 1:
            # config.py requires at least one CIF; refuse rather than write junk.
            self._cif_message(CIF_LAST_REMAINS)
            return
        button = self._cif_buttons.pop(cif)
        self._chip_row.removeWidget(button)
        button.deleteLater()
        self._cif_message("")
        self._update_save_enabled()

    def _on_add_cif(self) -> None:
        # Mirrors config.py's validator so the form can never offer a CIF the
        # config would reject.
        cif = self._add_cif_edit.text().strip().upper().removeprefix("RO")
        if not cif or not cif.isdigit():
            self._cif_message(CIF_INVALID)
            return
        if cif in self._cif_buttons:
            self._cif_message(CIF_DUPLICATE)
            return
        self._add_chip(cif)
        self._add_cif_edit.clear()
        self._cif_message("")
        self._update_save_enabled()

    def _on_lookback(self, value: int) -> None:
        self._lookback_label.setText(fmt.noun(value, "zi", "zile"))

    def _on_choose_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, _DIR_LABEL)
        if chosen:
            self._directory.setText(chosen)

    def _insert_into_template(self, text: str) -> None:
        """Splice a variable at the caret, replacing any selection, and refocus.

        ``QLineEdit.insert`` already does both halves of that; returning focus
        to the field is what keeps the panel an authoring tool rather than a
        detour — the user clicks a name and keeps typing.
        """
        self._template.insert(text)
        self._template.setFocus()

    def _refresh_preview(self) -> None:
        result = render_preview(self._template.text(), directory=self._directory.text())
        if result.ok:
            self._preview.setText("Previzualizare: " + result.text)
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
            and self._is_modified()
        )
        self._save_button.setEnabled(ok)

    def _is_modified(self) -> bool:
        return self._baseline_form is not None and (
            self._form() != self._baseline_form
            or self._frequency.currentIndex() != self._baseline_frequency
        )

    # -- read form state ------------------------------------------------------

    def _selected_cifs(self) -> list[str]:
        # Every chip present is followed — the list *is* the input.
        return list(self._cif_buttons)

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
            self._preview.setText(_NEEDS_INIT)
            return
        config_io.save(doc, self._config_path)
        self._reinstall_schedule_if_active()
        self._config = self._load_config()  # reload from disk, no cached copy
        self._baseline_form = self._form()
        self._baseline_frequency = self._frequency.currentIndex()
        self._update_save_enabled()
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

    def reload(self) -> None:
        """Rebuild the form from ``config.toml``, discarding any pending edits.

        The window calls this every time it opens, so a cancelled session can
        never leak into the next one.
        """
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
        self._artifact_cards.clear()
        self._baseline_form = None
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
#sectionHeader {{ color:{theme.faint}; font-size:11px; letter-spacing:1px;
    font-weight:700; }}
#sectionRule {{ background-color:{theme.border}; border:none; }}
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
/* A chip's presence *is* the selection, so it always reads as active. */
#cifChip {{ background-color:{theme.accent_soft_bg};
    color:{theme.accent}; border-color:{theme.accent}; }}
#cifError {{ color:{theme.red}; font-size:11px; }}
#artifactCard {{ border:1px solid {theme.border}; border-radius:6px;
    background-color:{theme.panel_bg}; }}
#artifactCard[checked="true"] {{ border-color:{theme.accent};
    background-color:{theme.accent_soft_bg}; }}
/* The blanket QWidget background above would otherwise paint over the card. */
#artifactCard QLabel, #artifactCard QCheckBox {{ background:transparent; }}
#saveBar {{ background-color:{theme.window_bg};
    border-top:1px solid {theme.border}; }}
#saveNote {{ color:{theme.faint}; font-size:11px; }}
#savePrimary {{ background-color:{theme.accent}; color:{theme.on_accent};
    border:none; border-radius:6px; padding:6px 12px; }}
#savePrimary:disabled {{ background-color:{theme.border};
    color:{theme.faint}; }}
#scheduleActive {{ color:{theme.green}; font-size:12.5px; }}
#scheduleInactive {{ color:{theme.muted}; font-size:12.5px; }}
""" + template_help_qss(theme)
