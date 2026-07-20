"""Archive database: dedupe gate, path registry, catalog, failure traces."""

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from anaf_sync.state import Archive, CatalogEntry


def _entry(message_id: str, base_path: str, **over: object) -> CatalogEntry:
    fields: dict[str, object] = {
        "message_id": message_id,
        "cif": "111",
        "direction": "received",
        "base_path": base_path,
        "artifacts": ["zip", "xml"],
    }
    fields.update(over)
    return CatalogEntry(**fields)  # type: ignore[arg-type]


def test_fresh_open_creates_schema_and_reopen_sees_data(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.db"
    with Archive.open(path) as archive:
        assert not archive.is_archived("m1")
        assert archive.count == 0
        archive.record(_entry("m1", "123/received/f1"))

    with Archive.open(path) as archive:
        assert archive.is_archived("m1")
        assert archive.count == 1


def test_schema_version_mismatch_raises(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with Archive.open(path):
        pass
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="schema version"):
        Archive.open(path)


def test_record_roundtrips_all_catalog_fields(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    entry = _entry(
        "m1",
        "123/received/f1",
        issue_date=dt.date(2026, 7, 3),
        number="FCT-1001",
        partner_name="ACME SRL",
        partner_cif="222",
        total=1234.56,
        currency="RON",
        message_type="FACTURA PRIMITA",
    )
    with Archive.open(path) as archive:
        archive.record(entry)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM messages WHERE message_id = 'm1'").fetchone()
    conn.close()
    assert row["cif"] == "111"
    assert row["direction"] == "received"
    assert row["base_path"] == "123/received/f1"
    assert row["artifacts"] == '["zip", "xml"]'
    assert row["issue_date"] == "2026-07-03"
    assert row["number"] == "FCT-1001"
    assert row["partner_name"] == "ACME SRL"
    assert row["partner_cif"] == "222"
    assert row["total"] == 1234.56
    assert row["currency"] == "RON"
    assert row["message_type"] == "FACTURA PRIMITA"
    assert row["saved_at"]  # stamped by record()


def test_record_tolerates_all_optional_fields_none(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record(_entry("m1", "p", artifacts=[]))
        assert archive.is_archived("m1")


def test_re_recording_at_new_base_frees_the_old_path(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record(_entry("m1", "a/b"))
        assert archive.claim_base(Path("a/b"), "other") == Path("a/b_other")

        archive.record(_entry("m1", "a/c"))  # re-archived under a new base
        # The old base is released; a different message may now claim it as-is.
        assert archive.claim_base(Path("a/b"), "other") == Path("a/b")
        assert archive.claim_base(Path("a/c"), "other") == Path("a/c_other")
        assert archive.count == 1  # still one message, not two


def test_claim_base_unowned_self_and_foreign(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        # Unowned base: claimed as-is.
        assert archive.claim_base(Path("x/y"), "m1") == Path("x/y")
        archive.record(_entry("m1", "x/y"))
        # Owned by the same message (e.g. --redownload): as-is, overwritten.
        assert archive.claim_base(Path("x/y"), "m1") == Path("x/y")
        # Owned by another message: suffixed.
        assert archive.claim_base(Path("x/y"), "m2") == Path("x/y_m2")


def test_failure_insert_then_bump(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with Archive.open(path) as archive:
        archive.record_failure("m1", "first error")
        first = archive.failures["m1"]
        assert first.attempts == 1
        assert first.error == "first error"

        archive.record_failure("m1", "second error")

    failure = Archive.open(path).failures["m1"]
    assert failure.attempts == 2
    assert failure.error == "second error"
    assert failure.first_failed_at <= failure.last_failed_at


def test_record_clears_the_failure_trace(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record_failure("m1", "boom")
        assert "m1" in archive.failures
        archive.record(_entry("m1", "p"))
        assert "m1" not in archive.failures


def test_retention_drops_only_stale_failures_never_messages(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=120)).isoformat()
    with Archive.open(path) as archive:
        archive.record(_entry("kept", "p"))
        archive.record_failure("recent", "boom")
    # Backdate one failure past the retention window.
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO failures (message_id, first_failed_at, last_failed_at, "
        "attempts, error) VALUES ('stale', ?, ?, 5, 'old')",
        (old, old),
    )
    conn.commit()
    conn.close()

    with Archive.open(path, failure_retention=dt.timedelta(days=90)) as archive:
        assert "stale" not in archive.failures
        assert "recent" in archive.failures
        assert archive.is_archived("kept")  # messages are never pruned


def test_open_without_retention_prunes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=120)).isoformat()
    with Archive.open(path):
        pass
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO failures (message_id, first_failed_at, last_failed_at, "
        "attempts, error) VALUES ('stale', ?, ?, 5, 'old')",
        (old, old),
    )
    conn.commit()
    conn.close()

    with Archive.open(path) as archive:  # no retention passed
        assert "stale" in archive.failures


def test_mutations_commit_before_returning(tmp_path: Path) -> None:
    """A second connection sees each change — proxy for crash-safety."""
    path = tmp_path / "state.db"
    with Archive.open(path) as archive:
        archive.record(_entry("m1", "p"))
        # A separate connection (the "next run after a crash") sees the commit.
        assert Archive.open(path).is_archived("m1")

        archive.record_failure("m2", "boom")
        assert "m2" in Archive.open(path).failures
