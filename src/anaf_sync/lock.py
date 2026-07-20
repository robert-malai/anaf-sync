"""Single-instance lock so overlapping scheduled runs cannot race.

A slow pass (large window, rate-limit backoff) can outlive the interval to the
next scheduled run. Two concurrent passes would both write the same artifact
files to the same rendered paths — interleaving on disk, since those writes do
not go through the archive DB — burn ANAF's daily call quota on duplicate
downloads, and contend on the DB itself. The archive's per-transaction commits
cannot serialize a whole run; this lock does, wrapping the OS advisory lock
(``flock`` / ``msvcrt.locking``) via ``filelock`` so it releases automatically
however the process dies — no stale-pidfile handling needed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

__all__ = ["LockHeldError", "sync_lock"]


class LockHeldError(RuntimeError):
    """Another anaf-sync run already holds the lock."""


@contextmanager
def sync_lock(path: Path) -> Iterator[None]:
    """Hold the exclusive run lock at ``path`` for the duration of the block.

    Raises:
        LockHeldError: another process holds the lock right now.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(path, timeout=0, mode=0o600)
    try:
        lock.acquire()
    except Timeout:
        raise LockHeldError(
            f"another anaf-sync run is already in progress (lock: {path})"
        ) from None
    try:
        yield
    finally:
        lock.release()
