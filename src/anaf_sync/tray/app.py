"""The tray application: a status icon, a menu, and the wiring between them.

``run`` is the real entry point (the package ``__init__`` guards the PySide6
import and delegates here). It is a single-instance, menu-bar-only app that
observes ``state.db`` and offers to spawn a sync — no window yet (that arrives
in M2). All display logic lives in the pure :mod:`status` / :mod:`strings` /
:mod:`theme` modules; this file is the thin Qt assembly over them.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..config import default_config_path, default_state_path
from . import strings
from .icons import status_icon
from .runner import SyncRunner
from .status import TrayStatus, load_status
from .theme import (
    MONO_FONT_FAMILY,
    RADIUS_CHIP,
    Theme,
    current_theme,
    menu_qss,
    on_scheme_changed,
    status_bg,
    status_color,
)
from .watcher import StateWatcher

__all__ = ["TrayApp", "main", "run"]

_MENU_WIDTH = 300
_DOT = 9  # status-dot diameter, px


class TrayApp:
    """Owns the tray icon, its menu, and the sync/watch machinery."""

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        self._state_path = state_path or default_state_path()
        self._config_path = config_path or default_config_path()
        self._output_dir: Path | None = None
        self._theme: Theme = current_theme()

        self._tray = QSystemTrayIcon()
        self._menu = QMenu()
        self._tray.setContextMenu(self._menu)

        self._runner = SyncRunner()
        self._runner.started.connect(self._on_sync_started)
        self._runner.finished.connect(self._on_sync_finished)

        self._watcher = StateWatcher(self._state_path)
        self._watcher.changed.connect(self.refresh)

        self._build_menu()
        on_scheme_changed(self._on_theme_changed)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self.refresh()
        self._tray.show()
        self._watcher.start()

    # -- menu construction ----------------------------------------------------

    def _build_menu(self) -> None:
        self._header_action = QWidgetAction(self._menu)
        self._header_action.setDefaultWidget(self._build_header())
        self._menu.addAction(self._header_action)
        self._menu.addSeparator()

        self._sync_action = QAction(strings.MENU_SYNC_NOW, self._menu)
        self._sync_action.triggered.connect(lambda: self._runner.start())
        self._menu.addAction(self._sync_action)

        self._archived_action = QAction(strings.MENU_ARCHIVED_INVOICES, self._menu)
        self._archived_action.setEnabled(False)  # opens the window in M2
        self._menu.addAction(self._archived_action)

        self._folder_action = QAction(strings.MENU_OPEN_FOLDER, self._menu)
        self._folder_action.triggered.connect(self._open_folder)
        self._menu.addAction(self._folder_action)

        self._menu.addSeparator()
        self._settings_action = QAction(strings.MENU_SETTINGS, self._menu)
        self._settings_action.setVisible(False)  # arrives in M3
        self._menu.addAction(self._settings_action)

        self._menu.addSeparator()
        quit_action = QAction(strings.MENU_QUIT, self._menu)
        quit_action.triggered.connect(self._quit)
        self._menu.addAction(quit_action)

    def _build_header(self) -> QWidget:
        container = QWidget()
        container.setMinimumWidth(_MENU_WIDTH)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        top = QWidget()
        top_layout = QVBoxLayout(top)  # dot sits inline via rich-text label
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(2)
        self._headline_label = QLabel()
        self._headline_label.setTextFormat(Qt.TextFormat.RichText)
        self._subline_label = QLabel()
        self._subline_label.setWordWrap(True)
        top_layout.addWidget(self._headline_label)
        top_layout.addWidget(self._subline_label)
        layout.addWidget(top)

        self._alert_frame = QFrame()
        alert_layout = QVBoxLayout(self._alert_frame)
        alert_layout.setContentsMargins(10, 7, 10, 7)
        self._alert_label = QLabel()
        self._alert_label.setWordWrap(True)
        self._alert_label.setTextFormat(Qt.TextFormat.RichText)
        alert_layout.addWidget(self._alert_label)
        layout.addWidget(self._alert_frame)

        return container

    # -- refresh --------------------------------------------------------------

    def refresh(self) -> None:
        status = load_status(
            state_path=self._state_path,
            config_path=self._config_path,
            now=dt.datetime.now(dt.UTC),
        )
        self._output_dir = status.output_dir
        self._apply(status)

    def _apply(self, status: TrayStatus) -> None:
        theme = self._theme
        self._tray.setIcon(status_icon(status.state, theme=theme))
        self._tray.setToolTip(f"anaf-sync — {status.headline}")
        self._menu.setStyleSheet(menu_qss(theme))

        dot = _dot_html(status_color(theme, status.state))
        self._headline_label.setText(
            f'{dot} <span style="font-weight:700; color:{theme.text};">'
            f"{_escape(status.headline)}</span>"
        )
        self._subline_label.setStyleSheet(f"color:{theme.muted}; font-size:12px;")
        self._subline_label.setText(status.subline)

        self._apply_alert(status, theme)

        self._archived_action.setText(
            f"{strings.MENU_ARCHIVED_INVOICES}  {status.archived_count}"
        )
        self._folder_action.setEnabled(status.output_dir is not None)

    def _apply_alert(self, status: TrayStatus, theme: Theme) -> None:
        if status.alert_text is None:
            self._alert_frame.setVisible(False)
            return
        self._alert_frame.setVisible(True)
        self._alert_frame.setStyleSheet(
            f"background-color:{status_bg(theme, status.alert_state)};"
            f"border-radius:{RADIUS_CHIP}px;"
        )
        color = status_color(theme, status.alert_state)
        body = _escape(status.alert_text)
        if status.alert_command:
            body += _chip_html(status.alert_command, theme)
        self._alert_label.setStyleSheet(f"color:{color}; font-size:12px;")
        self._alert_label.setText(body)

    # -- actions --------------------------------------------------------------

    def _on_sync_started(self) -> None:
        self._sync_action.setText(strings.MENU_SYNCING)
        self._sync_action.setEnabled(False)

    def _on_sync_finished(self, _exit_code: int) -> None:
        self._sync_action.setText(strings.MENU_SYNC_NOW)
        self._sync_action.setEnabled(True)
        self.refresh()

    def _on_theme_changed(self, theme: Theme) -> None:
        self._theme = theme
        self.refresh()

    def _open_folder(self) -> None:
        if self._output_dir is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_dir)))

    def _quit(self) -> None:
        self._watcher.stop()
        self._tray.hide()
        QApplication.quit()


def _dot_html(color: str) -> str:
    """A status dot inline in a rich-text label (● glyph, coloured)."""
    return f'<span style="color:{color}; font-size:{_DOT}px;">●</span>'


def _chip_html(command: str, theme: Theme) -> str:
    return (
        f' <span style="font-family:{MONO_FONT_FAMILY}; '
        f"background-color:{theme.mono_chip_bg}; color:{theme.text};"
        f'padding:1px 4px; border-radius:4px;">{_escape(command)}</span>'
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run() -> int:
    """Launch the single-instance tray app; returns the Qt exit code."""
    from filelock import FileLock, Timeout

    lock_path = default_state_path().with_name("tray.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return 0  # another instance already owns the tray

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("system tray is not available on this platform", file=sys.stderr)

    tray_app = TrayApp()
    tray_app.start()
    try:
        return int(app.exec())
    finally:
        lock.release()


def main() -> int:
    """Alias kept for symmetry; the package ``__init__`` is the guard entry."""
    return run()
