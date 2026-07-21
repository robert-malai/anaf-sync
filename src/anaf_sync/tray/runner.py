"""Run ``anaf-sync sync`` from the tray, without reimplementing the sync.

The tray never syncs in-process: it spawns the same console script the OS
scheduler runs (resolved the way :mod:`anaf_sync.scheduling` resolves it), so
there is exactly one sync code path. The real serialisation against a scheduled
run is the file lock in :mod:`anaf_sync.lock`; the in-flight guard here is
cosmetic — it just stops the menu firing a second child while one is visible.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, Signal

from ..scheduling import sync_executable

__all__ = ["SyncRunner"]


class SyncRunner(QObject):
    """Spawns ``anaf-sync sync`` as a child process and reports completion."""

    started = Signal()
    finished = Signal(int)  # process exit code

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self) -> None:
        """Launch a sync; a no-op while one is already running."""
        if self.running:
            return
        process = QProcess(self)
        process.setProgram(str(sync_executable()))
        process.setArguments(["sync"])
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start()
        self.started.emit()

    def _on_finished(self, exit_code: int, _status: object) -> None:
        if self._process is None:  # already handled by _on_error
            return
        self._process = None
        self.finished.emit(exit_code)

    def _on_error(self, _error: object) -> None:
        # A failure to even launch (e.g. the script vanished) still has to clear
        # the guard and refresh the UI; report a non-zero code. Whichever of
        # error/finished fires first wins; the guard makes the other a no-op.
        if self._process is None:
            return
        self._process = None
        self.finished.emit(1)
