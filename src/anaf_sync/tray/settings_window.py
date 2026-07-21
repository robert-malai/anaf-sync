"""The Setări window — a second top-level window, not a page of a stack.

The catalog is a surface the user leaves open; Setări is a bounded editing
task with an explicit commit boundary, so it gets its own window and its own
two exits (DESIGN.md §10). *Salvează modificările* writes ``config.toml`` and
closes; *Renunță*, Esc and the window's close button are one reject path that
closes without writing. It is a ``QDialog`` purely for that reject wiring —
modeless, so Facturi stays usable behind it.

Unlike the catalog it has a **maximum** size: a form holds a fixed amount of
content, and past the point where all of it fits, extra pixels are empty space
with a save bar stranded at the bottom.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from . import store
from .settings_view import SettingsView
from .theme import Theme, current_theme, window_qss

__all__ = ["SettingsWindow"]

_TITLE = "Setări — anaf-sync"
#: The design size, which is also the minimum.
_MIN_WIDTH, _MIN_HEIGHT = 760, 620
#: The width fits five artifact cards on one row legibly; the height is where
#: the form stops scrolling at the *narrowest* allowed width, so at maximum
#: height nothing scrolls whatever the width.
_MAX_WIDTH, _MAX_HEIGHT = 1200, 780

_GEOMETRY_KEY = "setari/geometry"


class SettingsWindow(QDialog):
    """Hosts :class:`SettingsView`; closes on save and on cancel alike."""

    #: Re-emitted from the view after a successful write, before closing.
    saved = Signal()

    def __init__(
        self,
        *,
        state_path: Path,
        config_path: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(_MIN_WIDTH, _MIN_HEIGHT)
        self.setMaximumSize(_MAX_WIDTH, _MAX_HEIGHT)
        self.setSizeGripEnabled(True)
        self._theme: Theme = current_theme()

        self._view = SettingsView(state_path=state_path, config_path=config_path)
        self._view.saved.connect(self._on_saved)
        self._view.cancelled.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view)

        self.set_theme(self._theme)
        self._restore_geometry()

    # -- lifecycle ------------------------------------------------------------

    def open_fresh(self) -> None:
        """Show the window with the form re-read from disk, and raise it."""
        self._view.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_saved(self) -> None:
        self.saved.emit()
        self.accept()

    def done(self, result: int) -> None:
        # Every exit — save, Renunță, Esc, the close button — funnels through
        # QDialog.done(), so geometry is persisted in exactly one place.
        self.save_geometry_to_settings()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.save_geometry_to_settings()
        super().closeEvent(event)

    # -- geometry -------------------------------------------------------------

    def _restore_geometry(self) -> None:
        blob = store.geometry_settings().value(_GEOMETRY_KEY)
        if isinstance(blob, QByteArray):
            self.restoreGeometry(blob)

    def save_geometry_to_settings(self) -> None:
        """Persist this window's own geometry (its key, not the catalog's)."""
        store.geometry_settings().setValue(_GEOMETRY_KEY, self.saveGeometry())

    # -- theme ----------------------------------------------------------------

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(window_qss(theme))
        self._view.set_theme(theme)
