"""Watch ``state.db`` for changes so the tray refreshes without polling hard.

A ``QFileSystemWatcher`` fires on writes to the database and its WAL sidecar; a
500 ms debounce collapses the burst a single sync commit produces into one
refresh. Because an atomic replace makes the watcher drop the old file, paths
are re-added on every event, and a 60 s poll is kept as a backstop for any
event the platform misses.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

__all__ = ["StateWatcher"]

_DEBOUNCE_MS = 500
_POLL_MS = 60_000


class StateWatcher(QObject):
    """Emits :attr:`changed` (debounced) whenever ``state.db`` is written."""

    changed = Signal()

    def __init__(self, state_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state_path = state_path
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_event)
        self._watcher.directoryChanged.connect(self._on_event)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.changed)

        self._poll = QTimer(self)
        self._poll.setInterval(_POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    def start(self) -> None:
        self._add_paths()
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()
        self._debounce.stop()

    def _targets(self) -> list[Path]:
        name = self._state_path.name
        return [
            self._state_path,
            self._state_path.with_name(f"{name}-wal"),
            self._state_path.parent,  # catches (re)creation of the db file
        ]

    def _add_paths(self) -> None:
        watched = set(self._watcher.files()) | set(self._watcher.directories())
        for target in self._targets():
            if target.exists() and str(target) not in watched:
                self._watcher.addPath(str(target))

    def _on_event(self, _path: str) -> None:
        # Re-add first: an atomic os.replace drops the watched inode.
        self._add_paths()
        self._debounce.start()

    def _on_poll(self) -> None:
        self._add_paths()
        self.changed.emit()
