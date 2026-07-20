"""The single-instance run lock."""

from pathlib import Path

import pytest

from anaf_sync.lock import LockHeldError, sync_lock


def test_lock_is_exclusive_and_released(tmp_path: Path) -> None:
    path = tmp_path / "sync.lock"
    with (
        sync_lock(path),
        pytest.raises(LockHeldError, match="already in progress"),
        sync_lock(path),
    ):
        pass
    with sync_lock(path):  # released after the block, not left stale
        pass


def test_lock_creates_parent_directory(tmp_path: Path) -> None:
    with sync_lock(tmp_path / "nested" / "sync.lock"):
        pass
