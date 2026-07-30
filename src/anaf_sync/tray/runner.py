"""Run an ``anaf-sync`` subcommand from the tray, without reimplementing it.

The tray never does the work in-process: it spawns the same console script the
OS scheduler runs (resolved the way :mod:`anaf_sync.scheduling` resolves it), so
there is exactly one code path per command — ``sync`` for the menu's
"Sincronizează acum" and the failing-message retry, ``reprocess`` for the
Facturi window's per-invoice button. The real serialisation against a scheduled
run is the file lock in :mod:`anaf_sync.lock`; the in-flight guard here is
cosmetic, and deliberately one guard across *all* subcommands — two tray-spawned
children would only meet at that lock anyway, and the second would die on it.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, Signal

from ..scheduling import sync_executable

__all__ = ["CliRunner"]


class CliRunner(QObject):
    """Spawns one ``anaf-sync`` subcommand as a child and reports completion."""

    started = Signal(str)  # the subcommand, so the UI can say which one runs
    finished = Signal(int)  # process exit code

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    def start(self, *arguments: str) -> None:
        """Launch ``anaf-sync <arguments>``; a no-op while one is running."""
        if self.running or not arguments:
            return
        process = QProcess(self)
        process.setProgram(str(sync_executable()))
        process.setArguments(list(arguments))
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        process.start()
        self.started.emit(arguments[0])

    def sync(self) -> None:
        """Launch a plain sync — the menu's action, and the retry button's."""
        self.start("sync")

    def reprocess(self, message_id: str) -> None:
        """Re-derive one archived message from its own files, path included.

        ``--move`` is deliberate: leaving the invoice in the ``unknown`` folder
        its unreadable projection created would fix only half of what the
        operator can see, and send them to a terminal for the other half.
        """
        self.start("reprocess", "--move", "--message-id", message_id)

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
