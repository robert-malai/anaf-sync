"""The right-hand details pane — normal, delayed, and failing variants.

Rebuilt on each selection from the pure record the model hands over. It emits
intent signals (open the PDF, reveal in the file manager, retry the sync) that
the window wires to real actions; it never touches the archive or the network
itself. Shared phrases come from :mod:`format`, colours from the active
:class:`Theme`.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..health import DELAY_THRESHOLD_DAYS, upload_delay_days
from ..state import CatalogEntry
from . import format as fmt
from .models import FailureRow
from .theme import LIGHT, MONO_FONT_FAMILY, RADIUS_CHIP, RADIUS_PANEL, Theme

__all__ = ["DetailsPane", "artifact_path"]

_WIDTH = 250

_FILE_MISSING_TOOLTIP = "fișierul nu a fost găsit pe disc"


def artifact_path(base_path: str, extension: str) -> Path:
    """The on-disk path of one artifact, matching how the engine names them.

    The engine writes each artifact as ``base.with_suffix(ext)``; reproducing
    the exact operation here keeps the two in step even when the base name
    itself contains dots (e.g. ``ACME S.R.L.``).
    """
    return Path(base_path).with_suffix(extension)


class DetailsPane(QWidget):
    """Shows the selected invoice's facts, files, and provenance."""

    open_pdf_requested = Signal(object)  # Path
    reveal_requested = Signal(object)  # Path
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_WIDTH)
        self._theme: Theme = LIGHT
        self._current: CatalogEntry | FailureRow | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(10)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.show_empty()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.show_record(self._current)

    def show_record(self, record: CatalogEntry | FailureRow | None) -> None:
        self._current = record
        if record is None:
            self.show_empty()
        elif isinstance(record, FailureRow):
            self._show_failure(record)
        else:
            self._show_catalog(record)

    # -- variants -------------------------------------------------------------

    def show_empty(self) -> None:
        self._reset()
        self._layout.addWidget(
            self._muted("Selectați o factură pentru detalii.", wrap=True)
        )

    def _show_catalog(self, entry: CatalogEntry) -> None:
        self._reset()
        self._layout.addWidget(self._title(entry.number or fmt.EM_DASH))
        self._layout.addWidget(self._pill(entry.direction))

        delay = upload_delay_days(entry.issue_date, entry.created_at)
        if delay is not None and delay > DELAY_THRESHOLD_DAYS:
            self._layout.addWidget(self._delayed_panel(entry, delay))

        self._add_facts(
            ("Partener", entry.partner_name or fmt.EM_DASH),
            ("CIF partener", fmt.provenance(entry.partner_cif)),
            ("Data emiterii", fmt.short_date(entry.issue_date)),
            ("Încărcată în SPV", fmt.short_date(_spv_date(entry))),
            ("Total", fmt.money(entry.total, entry.currency)),
        )
        self._add_files(entry)
        self._add_path_box(entry.base_path)
        self._add_buttons(entry)
        self._add_provenance(entry.message_id, entry.message_type, entry.created_at)

    def _show_failure(self, row: FailureRow) -> None:
        self._reset()
        # Partner is unknown until the message downloads; the id is what we have.
        self._layout.addWidget(self._title(row.message_id))
        self._layout.addWidget(self._pill("failing"))
        self._layout.addWidget(self._failing_panel(row))

        retry = self._button("Reîncearcă acum", primary=True, danger=True)
        retry.clicked.connect(lambda: self.retry_requested.emit())
        self._layout.addWidget(retry)

        self._add_provenance(row.message_id, None, None)

    # -- panels ---------------------------------------------------------------

    def _delayed_panel(self, entry: CatalogEntry, delay: int) -> QWidget:
        after = fmt.noun(delay, "zi", "zile")
        limit = fmt.noun(DELAY_THRESHOLD_DAYS, "zi", "zile")
        body = (
            f"Emisă {fmt.short_date(entry.issue_date)} · încărcată în SPV "
            f"{fmt.short_date(_spv_date(entry))} — după {after} (limita: {limit})"
        )
        return self._panel(
            "Declarată cu întârziere", [body], self._theme.amber, self._theme.amber_bg
        )

    def _failing_panel(self, row: FailureRow) -> QWidget:
        first = fmt.short_date(row.record.first_failed_at.date())
        attempts = fmt.noun(row.record.attempts, "încercare", "încercări")
        lines = [
            f"Eșuează din {first} · {attempts}",
            f"Ultima eroare: {row.record.error}",
            fmt.spv_expiry(row.days_left).capitalize(),
        ]
        return self._panel(
            "Descărcarea eșuează repetat", lines, self._theme.red, self._theme.red_bg
        )

    def _panel(self, title: str, lines: list[str], color: str, bg: str) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(
            f"background-color:{bg}; border:1px solid {color};"
            f"border-radius:{RADIUS_PANEL}px;"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        heading = QLabel(title)
        heading.setStyleSheet(f"color:{color}; font-weight:700;")
        layout.addWidget(heading)
        for line in lines:
            label = QLabel(line)
            label.setWordWrap(True)
            label.setStyleSheet(f"color:{color}; font-size:12px;")
            layout.addWidget(label)
        return frame

    # -- rows -----------------------------------------------------------------

    def _add_facts(self, *rows: tuple[str, str]) -> None:
        for label, value in rows:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            key = QLabel(label)
            key.setStyleSheet(f"color:{self._theme.muted}; font-size:12px;")
            val = QLabel(value)
            val.setStyleSheet(f"color:{self._theme.text}; font-size:12px;")
            val.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            val.setWordWrap(True)
            layout.addWidget(key)
            layout.addStretch(1)
            layout.addWidget(val)
            self._layout.addWidget(row)

    def _add_files(self, entry: CatalogEntry) -> None:
        if not entry.artifacts:
            return
        self._layout.addWidget(self._section_label("Fișiere pe disc"))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for artifact in entry.artifacts:
            layout.addWidget(self._chip(f".{artifact}"))
        layout.addStretch(1)
        self._layout.addWidget(row)

    def _add_path_box(self, base_path: str) -> None:
        box = QLabel(base_path)
        box.setWordWrap(True)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.setStyleSheet(
            f"font-family:{MONO_FONT_FAMILY}; font-size:10.5px;"
            f"background-color:{self._theme.mono_chip_bg};"
            f"color:{self._theme.text}; border-radius:{RADIUS_CHIP}px; padding:6px;"
        )
        self._layout.addWidget(box)

    def _add_buttons(self, entry: CatalogEntry) -> None:
        pdf = artifact_path(entry.base_path, ".pdf")
        open_btn = self._button("Deschide PDF", primary=True)
        if "pdf" in entry.artifacts and pdf.exists():
            open_btn.clicked.connect(lambda: self.open_pdf_requested.emit(pdf))
        else:
            open_btn.setEnabled(False)
            open_btn.setToolTip(_FILE_MISSING_TOOLTIP)

        reveal_btn = self._button("Arată în dosar")
        folder = Path(entry.base_path).parent
        if folder.exists():
            reveal_btn.clicked.connect(
                lambda: self.reveal_requested.emit(_reveal_target(entry))
            )
        else:
            reveal_btn.setEnabled(False)
            reveal_btn.setToolTip(_FILE_MISSING_TOOLTIP)

        # Side by side, as in the mockup — stacking them would push the
        # provenance block below the fold on a short window.
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for btn in (open_btn, reveal_btn):
            layout.addWidget(btn, 1)
        self._layout.addWidget(row)

    def _add_provenance(
        self,
        message_id: str,
        message_type: str | None,
        archived: dt.datetime | None,
    ) -> None:
        rule = QFrame()
        rule.setFrameShape(QFrame.Shape.HLine)
        rule.setStyleSheet(f"color:{self._theme.border};")
        self._layout.addWidget(rule)
        for label, value in (
            ("message_id", fmt.provenance(message_id)),
            ("tip mesaj", fmt.provenance(message_type)),
            ("arhivat la", fmt.archived_at(archived)),
        ):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            key = QLabel(label)
            key.setStyleSheet(f"color:{self._theme.faint}; font-size:11px;")
            val = QLabel(value)
            val.setStyleSheet(
                f"font-family:{MONO_FONT_FAMILY}; color:{self._theme.muted};"
                "font-size:11px;"
            )
            layout.addWidget(key)
            layout.addStretch(1)
            layout.addWidget(val)
            self._layout.addWidget(row)

    # -- primitives -----------------------------------------------------------

    def _title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color:{self._theme.text}; font-size:15px; font-weight:700;"
        )
        return label

    def _pill(self, direction: object) -> QLabel:
        colors = {
            "received": self._theme.accent,
            "sent": self._theme.muted,
            "failing": self._theme.red,
        }
        color = colors.get(str(direction), self._theme.muted)
        pill = QLabel(fmt.direction_label(str(direction)))
        pill.setStyleSheet(f"color:{color}; font-size:11px; font-weight:700;")
        return pill

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color:{self._theme.muted}; font-size:11px;")
        return label

    def _chip(self, text: str) -> QLabel:
        chip = QLabel(text)
        chip.setStyleSheet(
            f"font-family:{MONO_FONT_FAMILY}; font-size:11px;"
            f"background-color:{self._theme.mono_chip_bg};"
            f"color:{self._theme.text}; border-radius:5px; padding:1px 5px;"
        )
        return chip

    def _muted(self, text: str, *, wrap: bool = False) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(wrap)
        label.setStyleSheet(f"color:{self._theme.muted}; font-size:12px;")
        return label

    def _button(
        self, text: str, *, primary: bool = False, danger: bool = False
    ) -> QPushButton:
        button = QPushButton(text)
        theme = self._theme
        if danger:
            bg, fg, border = theme.red, theme.on_accent, theme.red
        elif primary:
            bg, fg, border = theme.accent, theme.on_accent, theme.accent
        else:
            bg, fg, border = "transparent", theme.text, theme.border_strong
        button.setStyleSheet(
            f"QPushButton {{ background-color:{bg}; color:{fg};"
            f"border:1px solid {border}; border-radius:6px; padding:5px 10px; }}"
            f"QPushButton:disabled {{ color:{theme.faint};"
            f"border-color:{theme.border}; }}"
        )
        return button

    def _reset(self) -> None:
        _clear_layout(self._layout)


def _spv_date(entry: CatalogEntry) -> dt.date | None:
    return entry.created_at.date() if entry.created_at else None


def _reveal_target(entry: CatalogEntry) -> Path:
    """The file to select in the file manager: the PDF, else the ZIP, else dir."""
    for ext in (".pdf", ".zip"):
        candidate = artifact_path(entry.base_path, ext)
        if candidate.exists():
            return candidate
    return Path(entry.base_path).parent


def _clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
