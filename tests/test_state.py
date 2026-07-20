"""State-file persistence."""

import datetime as dt
import json
from pathlib import Path

from anaf_sync.state import SyncState


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    state = SyncState.load(path)
    assert not state.is_downloaded("m1")
    assert state.count == 0

    state.record("m1", "123/received/f1", ["zip", "xml"])

    # record() alone must persist — nothing else is called between messages.
    reloaded = SyncState.load(path)
    assert reloaded.is_downloaded("m1")
    assert reloaded.count == 1


def test_forget(tmp_path: Path) -> None:
    state = SyncState.load(tmp_path / "state.json")
    state.record("m1", "p", [])
    state.forget("m1")
    state.forget("never-seen")  # no-op
    assert not state.is_downloaded("m1")


def test_failure_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = SyncState.load(path)
    state.record_failure("m1", "first error")
    state.record_failure("m1", "second error")

    failure = SyncState.load(path).failures["m1"]
    assert failure.attempts == 2
    assert failure.error == "second error"
    assert failure.first_failed_at <= failure.last_failed_at


def test_prune_drops_stale_failures(tmp_path: Path) -> None:
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=120)).isoformat()
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "failures": {
                    "gone": {
                        "first_failed_at": old,
                        "last_failed_at": old,
                        "attempts": 5,
                        "error": "boom",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    state = SyncState.load(path)
    state.record_failure("recent", "boom")

    assert state.prune(dt.timedelta(days=90)) == 1
    assert "gone" not in state.failures
    assert "recent" in state.failures


def test_prune_drops_only_stale_records(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    saved_at = (dt.datetime.now(dt.UTC) - dt.timedelta(days=120)).isoformat()
    path.write_text(
        json.dumps(
            {
                "downloaded": {
                    "old": {"saved_at": saved_at, "base_path": "p", "artifacts": []}
                }
            }
        ),
        encoding="utf-8",
    )
    state = SyncState.load(path)
    state.record("fresh", "p", [])

    assert state.prune(dt.timedelta(days=90)) == 1
    assert not state.is_downloaded("old")
    assert state.is_downloaded("fresh")
    assert not SyncState.load(path).is_downloaded("old")  # prune persisted
    assert state.prune(dt.timedelta(days=90)) == 0  # second pass is a no-op
