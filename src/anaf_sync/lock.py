"""Single-instance lock so overlapping scheduled runs cannot race.

A slow pass (large window, rate-limit backoff) can outlive the interval to
the next scheduled run; two concurrent passes would download the same
messages twice and overwrite each other's ``state.json``. ``filelock``
wraps the OS advisory lock (``flock`` / ``msvcrt.locking``), so it is
released automatically however the process dies — no stale-pidfile
handling needed.
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
