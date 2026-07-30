"""Watch ``state.db`` for changes so the tray refreshes without polling hard.

A ``QFileSystemWatcher`` fires on writes to the database and its WAL sidecar; a
500 ms debounce collapses the burst a single sync commit produces into one
check. Because an atomic replace makes the watcher drop the old file, paths
are re-added on every event, and a 60 s poll is kept as a backstop for any
event the platform misses.

Filesystem events only say *look*, never *reload*: the tray's own read-only
opens write WAL read-marks into ``state.db-shm``, and the parent-directory
watch reports that write — emitting on raw events made every refresh schedule
the next one, an endless reset loop that jarred the catalog scrollbar every
500 ms. Whether the data actually moved is SQLite's own answer instead:
:class:`_DataVersionProbe` compares ``PRAGMA data_version`` on one long-lived
read-only connection, the counter that bumps exactly when another connection
commits.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

__all__ = ["StateWatcher"]

_DEBOUNCE_MS = 500
_POLL_MS = 60_000


class _DataVersionProbe:
    """Answers "has the database changed?" from SQLite itself.

    Holds one long-lived read-only connection — the tray's only persistent
    handle, carrying no locks between queries — and compares
    ``PRAGMA data_version``. Reads by other connections never move it, so the
    tray's own catalog queries can never look like new data. File stats are
    consulted only for *identity*: a deleted or re-created ``state.db`` would
    pin the connection to the old inode and report "no change" forever, so
    the connection follows the file, and either transition counts as a change.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None  # (st_dev, st_ino)
        self._version: int | None = None

    def prime(self) -> None:
        """Adopt the current database state without reporting it as a change."""
        self.changed()

    def changed(self) -> bool:
        """Whether the database changed since the last call (or ``prime``) —
        a commit by another connection, or the file itself appearing,
        vanishing, or being replaced."""
        if (identity := _identity_of(self._path)) is None:
            return self._forget()
        if self._conn is not None and identity == self._identity:
            return self._advanced()
        self._forget()
        return self._adopt(identity)

    def close(self) -> None:
        self._forget()

    def _forget(self) -> bool:
        """Drop the connection; True if a previously adopted database is gone."""
        had = self._identity is not None
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._identity = None
        self._version = None
        return had

    def _adopt(self, identity: tuple[int, int]) -> bool:
        """Follow a new (or re-created) database file; unreadable is not yet
        a change — the next event retries, and adoption reports it then."""
        try:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        except sqlite3.Error:
            return False
        try:
            version = _data_version(conn)
        except sqlite3.Error:
            conn.close()
            return False
        self._conn, self._identity, self._version = conn, identity, version
        return True

    def _advanced(self) -> bool:
        assert self._conn is not None  # guarded by the caller
        try:
            version = _data_version(self._conn)
        except sqlite3.Error:
            self._forget()  # re-adopt (and report) on the next event
            return False
        if version == self._version:
            return False
        self._version = version
        return True


def _identity_of(path: Path) -> tuple[int, int] | None:
    with contextlib.suppress(OSError):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino)
    return None


def _data_version(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA data_version").fetchone()[0]
    return int(version)


class StateWatcher(QObject):
    """Emits :attr:`changed` (debounced) whenever ``state.db`` gains new data."""

    changed = Signal()

    def __init__(self, state_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state_path = state_path
        self._probe = _DataVersionProbe(state_path)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_event)
        self._watcher.directoryChanged.connect(self._on_event)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._emit_if_changed)

        self._poll = QTimer(self)
        self._poll.setInterval(_POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    def start(self) -> None:
        self._add_paths()
        self._probe.prime()
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()
        self._debounce.stop()
        self._probe.close()

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

    def _emit_if_changed(self) -> None:
        if self._probe.changed():
            self.changed.emit()

    def _on_event(self, _path: str) -> None:
        # Re-add first: an atomic os.replace drops the watched inode.
        self._add_paths()
        self._debounce.start()

    def _on_poll(self) -> None:
        self._add_paths()
        self._emit_if_changed()
