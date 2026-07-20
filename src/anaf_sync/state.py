"""Persistent record of already-downloaded messages.

A small JSON file keyed by ANAF message id. It is what makes the scheduled run
idempotent: every run lists the full retention window and this file decides
what is new. Every mutation persists atomically before it returns, so a crash
mid-run never loses or duplicates work.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field

__all__ = ["DownloadRecord", "FailureRecord", "SyncState"]


class DownloadRecord(BaseModel):
    """What we know about one archived message."""

    saved_at: dt.datetime
    base_path: str
    artifacts: list[str] = []


class FailureRecord(BaseModel):
    """A message that keeps failing to download — kept for visibility only.

    Never gates retrying: the engine retries anything absent from the
    downloaded set on every run regardless. This exists so ``anaf-sync
    status`` can surface a persistent failure before ANAF's 60-day window
    closes on it.
    """

    first_failed_at: dt.datetime
    last_failed_at: dt.datetime
    attempts: int = 1
    error: str


class _StateFile(BaseModel):
    downloaded: dict[str, DownloadRecord] = Field(default_factory=dict)
    failures: dict[str, FailureRecord] = Field(default_factory=dict)


class SyncState:
    """The state file, loaded once per run.

    Durability is the class's contract, not the caller's: every mutating
    method rewrites the file atomically (temp file + rename) before
    returning, so callers cannot forget to save.
    """

    def __init__(self, path: Path, data: _StateFile) -> None:
        self._path = path
        self._data = data
        # Reverse index: which message owns which base path. The engine uses
        # it to keep two invoices that render the same template path apart.
        self._owners = {
            record.base_path: message_id
            for message_id, record in data.downloaded.items()
        }

    @classmethod
    def load(cls, path: Path) -> Self:
        if path.exists():
            data = _StateFile.model_validate_json(path.read_text(encoding="utf-8"))
        else:
            data = _StateFile()
        return cls(path, data)

    def is_downloaded(self, message_id: str) -> bool:
        return message_id in self._data.downloaded

    def record_of(self, message_id: str) -> DownloadRecord | None:
        return self._data.downloaded.get(message_id)

    def owner_of(self, base_path: str) -> str | None:
        """The message id whose archived artifacts live at ``base_path``."""
        return self._owners.get(base_path)

    def record(self, message_id: str, base_path: str, artifacts: list[str]) -> None:
        if (previous := self._data.downloaded.get(message_id)) is not None:
            self._owners.pop(previous.base_path, None)  # re-archived elsewhere
        self._data.downloaded[message_id] = DownloadRecord(
            saved_at=dt.datetime.now(dt.UTC),
            base_path=base_path,
            artifacts=artifacts,
        )
        self._owners[base_path] = message_id
        self._data.failures.pop(message_id, None)  # archived at last
        self._save()

    def record_failure(self, message_id: str, error: str) -> None:
        now = dt.datetime.now(dt.UTC)
        existing = self._data.failures.get(message_id)
        if existing is None:
            self._data.failures[message_id] = FailureRecord(
                first_failed_at=now, last_failed_at=now, error=error
            )
        else:
            existing.last_failed_at = now
            existing.attempts += 1
            existing.error = error
        self._save()

    def forget(self, message_id: str) -> None:
        if (record := self._data.downloaded.pop(message_id, None)) is not None:
            self._owners.pop(record.base_path, None)
            self._save()

    def prune(self, max_age: dt.timedelta) -> int:
        """Drop records older than ``max_age``; returns how many were removed.

        Only safe for ages beyond ANAF's 60-day retention window: such
        message ids can never appear in a listing again, so their records
        no longer gate anything. Pruning younger records would cause
        re-downloads on the next run. Failure records whose last attempt is
        that old have aged out of the listing window and go the same way.
        """
        cutoff = dt.datetime.now(dt.UTC) - max_age
        stale = [
            message_id
            for message_id, record in self._data.downloaded.items()
            if record.saved_at < cutoff
        ]
        for message_id in stale:
            self._owners.pop(self._data.downloaded[message_id].base_path, None)
            del self._data.downloaded[message_id]
        stale_failures = [
            message_id
            for message_id, failure in self._data.failures.items()
            if failure.last_failed_at < cutoff
        ]
        for message_id in stale_failures:
            del self._data.failures[message_id]
        removed = len(stale) + len(stale_failures)
        if removed:
            self._save()
        return removed

    @property
    def count(self) -> int:
        return len(self._data.downloaded)

    @property
    def failures(self) -> dict[str, FailureRecord]:
        return dict(self._data.failures)

    @property
    def path(self) -> Path:
        return self._path

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._data.model_dump_json(indent=2)
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".state-", suffix=".json"
        )
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)  # atomic against power loss, not just process death
        finally:
            os.close(fd)
        Path(tmp_name).replace(self._path)
        if os.name == "posix":  # rename durability needs a directory fsync
            dir_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
