"""Archive database: dedupe gate, path registry, catalog, failure traces."""

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from anaf_sync.state import (
    Archive,
    CatalogEntry,
    CatalogQuery,
    RunRecord,
    role_cifs,
)

# The pre-``created_at`` schema, verbatim, so the migration test starts from a
# real v1 database rather than a doctored v2 one.
_V1_SCHEMA = """
CREATE TABLE messages (
    message_id   TEXT PRIMARY KEY,
    cif          TEXT NOT NULL,
    direction    TEXT NOT NULL,
    saved_at     TEXT NOT NULL,
    base_path    TEXT NOT NULL UNIQUE,
    artifacts    TEXT NOT NULL,
    issue_date   TEXT,
    number       TEXT,
    partner_name TEXT,
    partner_cif  TEXT,
    total        REAL,
    currency     TEXT,
    message_type TEXT
);
CREATE TABLE failures (
    message_id      TEXT PRIMARY KEY,
    first_failed_at TEXT NOT NULL,
    last_failed_at  TEXT NOT NULL,
    attempts        INTEGER NOT NULL,
    error           TEXT NOT NULL
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta (key, value) VALUES ('schema_version', '1');
"""


def _make_v1_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_V1_SCHEMA)
    conn.execute(
        "INSERT INTO messages (message_id, cif, direction, saved_at, base_path, "
        "artifacts, issue_date, number, partner_name) "
        "VALUES ('old', '111', 'received', '2026-01-01T00:00:00', 'a/b', "
        "'[\"zip\"]', '2026-01-01', 'INV-1', 'OLD PARTNER SRL')"
    )
    conn.commit()
    conn.close()


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
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
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


def test_missing_pdf_lists_only_synced_rows_without_one(tmp_path: Path) -> None:
    archive = Archive.open(tmp_path / "state.db")
    archive.record(_entry("m1", "a/complete", artifacts=["zip", "pdf"]))
    archive.record(_entry("m2", "a/gap", artifacts=["zip"]))
    # A backfill row without a PDF is not repair's business (foreign folder).
    archive.record(
        _entry("backfill:x", "a/history", artifacts=["zip"], source="backfill")
    )

    assert [entry.message_id for entry in archive.missing_pdf()] == ["m2"]


def test_add_artifact_appends_once_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    archive = Archive.open(path)
    archive.record(_entry("m1", "a/gap", artifacts=["zip"]))

    archive.add_artifact("m1", "pdf")
    archive.add_artifact("m1", "pdf")  # idempotent — a re-run must not double it
    archive.add_artifact("ghost", "pdf")  # unknown id: a quiet no-op

    with Archive.open(path) as reopened:  # persisted, not just in memory
        assert reopened.catalog()[0].artifacts == ["zip", "pdf"]
        assert reopened.missing_pdf() == []


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


# -- M0.1 schema v2 migration + created_at -----------------------------------


def test_v1_db_migrates_in_place_and_keeps_old_rows(tmp_path: Path) -> None:
    """A v1 archive crosses every later migration in one open."""
    path = tmp_path / "state.db"
    _make_v1_db(path)

    with Archive.open(path) as archive:
        assert archive.is_archived("old")  # old row still readable
        entry = archive.catalog()[0]
        assert entry.message_id == "old"
        assert entry.created_at is None  # migrated rows keep NULL
        assert entry.source == "sync"  # pre-backfill rows were all downloaded

    conn = sqlite3.connect(path)
    version = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    conn.close()
    assert version == "3"
    assert {"created_at", "source"} <= cols


def test_v2_db_migrates_to_v3_and_marks_existing_rows_synced(tmp_path: Path) -> None:
    """Everything already in a v2 archive was downloaded, never backfilled.

    The ``NOT NULL DEFAULT 'sync'`` does that in the ``ALTER`` itself, so no
    row is left in a third "unknown provenance" state.
    """
    path = tmp_path / "state.db"
    with Archive.open(path) as archive:  # a v3 archive...
        archive.record(_entry("m1", "a/b"))
    conn = sqlite3.connect(path)  # ...wound back to v2
    conn.execute("ALTER TABLE messages DROP COLUMN source")
    conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    with Archive.open(path) as archive:
        assert archive.catalog()[0].source == "sync"
        assert archive.is_archived("m1")


def test_backfill_source_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with Archive.open(path) as archive:
        archive.record(_entry("backfill:abc", "a/b", source="backfill"))
        archive.record(_entry("m1", "c/d"))

    with Archive.open(path) as archive:
        by_id = {e.message_id: e.source for e in archive.catalog()}
        assert by_id == {"backfill:abc": "backfill", "m1": "sync"}


def test_created_at_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    created = dt.datetime(2026, 7, 18, 14, 30)
    with Archive.open(path) as archive:
        archive.record(_entry("m1", "p", created_at=created))

    with Archive.open(path) as archive:
        assert archive.catalog()[0].created_at == created


# -- M0.2 read-only open ------------------------------------------------------


def test_open_readonly_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="anaf-sync sync"):
        Archive.open_readonly(tmp_path / "absent.db")


def test_readonly_reads_while_writer_writes(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with Archive.open(path) as writer:
        writer.record(_entry("m1", "p1"))
        reader = Archive.open_readonly(path)
        try:
            assert reader.is_archived("m1")
            # A commit made after the reader opened is visible; no lock error.
            writer.record(_entry("m2", "p2"))
            assert reader.is_archived("m2")
            assert reader.count == 2
        finally:
            reader.close()


# -- M0.3 last-run record -----------------------------------------------------


def test_last_run_is_none_before_any_run(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        assert archive.last_run() is None


def test_run_record_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    run = RunRecord(
        finished_at=dt.datetime(2026, 7, 20, 6, 0, tzinfo=dt.UTC),
        outcome="failed",
        listed=12,
        archived=9,
        failures=1,
        error="auth expired",
        error_kind="AnafAuthError",
    )
    with Archive.open(path) as archive:
        archive.record_run(run)

    loaded = Archive.open_readonly(path).last_run()
    assert loaded == run


def test_record_run_overwrites_previous(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record_run(
            RunRecord(finished_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC), outcome="ok")
        )
        archive.record_run(
            RunRecord(
                finished_at=dt.datetime(2026, 7, 2, tzinfo=dt.UTC), outcome="crashed"
            )
        )
        last = archive.last_run()
        assert last is not None
        assert last.outcome == "crashed"


# -- M0.4 catalog query API ---------------------------------------------------


def _seed_catalog(archive: Archive) -> None:
    archive.record(
        _entry(
            "a",
            "pa",
            direction="received",
            issue_date=dt.date(2026, 7, 18),
            number="FCT-2107",
            partner_name="ELECTROMONTAJ CARPAȚI S.R.L.",
        )
    )
    archive.record(
        _entry(
            "b",
            "pb",
            direction="sent",
            issue_date=dt.date(2026, 7, 15),
            number="AS-1042",
            partner_name="MOBILA PRODEX S.R.L.",
        )
    )
    archive.record(
        _entry(
            "c",
            "pc",
            direction="received",
            issue_date=dt.date(2026, 7, 3),
            number="FCT-1001",
            partner_name="ACME CONSTRUCT S.R.L.",
        )
    )
    archive.record(
        _entry("d", "pd", direction="received", number="NO-DATE", partner_name="X SRL")
    )


def test_catalog_orders_newest_first_nulls_last(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_catalog(archive)
        ids = [e.message_id for e in archive.catalog()]
        assert ids == ["a", "b", "c", "d"]  # d has no issue_date → last


def test_catalog_filters_by_direction(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_catalog(archive)
        received = archive.catalog(CatalogQuery(directions={"received"}))
        assert {e.message_id for e in received} == {"a", "c", "d"}
        assert archive.catalog_count(CatalogQuery(directions={"sent"})) == 1
        # Both selected is the same as unfiltered; none selected matches
        # nothing rather than raising on "IN ()".
        both = CatalogQuery(directions={"received", "sent"})
        assert archive.catalog_count(both) == 4
        assert archive.catalog_count(CatalogQuery(directions=frozenset())) == 0


def test_catalog_search_matches_number_or_partner(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_catalog(archive)
        # Case-insensitive; matches partner name...
        assert {e.message_id for e in archive.catalog(CatalogQuery(search="acme"))} == {
            "c"
        }
        # ...and invoice number.
        assert {
            e.message_id for e in archive.catalog(CatalogQuery(search="AS-10"))
        } == {"b"}
        # "d" is "X SRL" (no dots), so only a/b/c match "S.R.L.".
        assert archive.catalog_count(CatalogQuery(search="S.R.L.")) == 3


def test_catalog_filters_by_issue_date_range(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_catalog(archive)
        window = archive.catalog(
            CatalogQuery(
                issued_from=dt.date(2026, 7, 10), issued_to=dt.date(2026, 7, 16)
            )
        )
        assert [e.message_id for e in window] == ["b"]  # only the 15th


def test_catalog_paging_with_limit_and_offset(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_catalog(archive)
        assert [e.message_id for e in archive.catalog(limit=2)] == ["a", "b"]
        assert [e.message_id for e in archive.catalog(limit=2, offset=2)] == ["c", "d"]
        assert archive.catalog_count() == 4


def test_distinct_cifs_sorted(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record(_entry("a", "pa", cif="87654321"))
        archive.record(_entry("b", "pb", cif="12345678"))
        archive.record(_entry("c", "pc", cif="12345678"))  # duplicate
        assert archive.distinct_cifs() == ["12345678", "87654321"]


def _seed_flow(archive: Archive) -> None:
    """Two followed CIFs, both directions — enough for the role columns to swap."""
    archive.record(
        _entry(
            "recv",
            "p-recv",
            cif="12345678",
            direction="received",
            partner_cif="14338501",
            issue_date=dt.date(2026, 7, 18),
            number="FCT-2107",
            total=4821.50,
        )
    )
    archive.record(
        _entry(
            "sent",
            "p-sent",
            cif="12345678",
            direction="sent",
            partner_cif="22518743",
            issue_date=dt.date(2026, 7, 15),
            number="AS-1042",
            total=12400.0,
        )
    )
    archive.record(
        _entry(
            "blind",
            "p-blind",
            cif="87654321",
            direction="received",
            issue_date=dt.date(2026, 7, 3),
            number="FCT-1001",
        )
    )


def test_role_cifs_swap_with_direction(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_flow(archive)
        # The followed CIF is the *recipient* of a received invoice...
        assert {
            e.message_id for e in archive.catalog(CatalogQuery(to_cif="12345678"))
        } == {"recv"}
        # ...and the *issuer* of a sent one, from the same stored column.
        assert {
            e.message_id for e in archive.catalog(CatalogQuery(from_cif="12345678"))
        } == {"sent"}
        # The partner's CIF mirrors it.
        assert {
            e.message_id for e in archive.catalog(CatalogQuery(from_cif="14338501"))
        } == {"recv"}
        assert {
            e.message_id for e in archive.catalog(CatalogQuery(to_cif="22518743"))
        } == {"sent"}


def test_role_cif_filter_skips_rows_without_a_partner_cif(tmp_path: Path) -> None:
    """A best-effort blank must not match — LIKE over NULL is NULL, not true."""
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_flow(archive)
        assert archive.catalog_count(CatalogQuery(from_cif="8765")) == 0


def test_catalog_sorts_by_any_whitelisted_column(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_flow(archive)
        by_number = archive.catalog(order_by="number", descending=False)
        assert [e.number for e in by_number] == ["AS-1042", "FCT-1001", "FCT-2107"]
        by_total = archive.catalog(order_by="total", descending=True)
        assert [e.message_id for e in by_total][:2] == ["sent", "recv"]


def test_catalog_sorts_blanks_last_in_both_directions(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_flow(archive)  # "blind" has no total
        for descending in (True, False):
            ordered = archive.catalog(order_by="total", descending=descending)
            assert ordered[-1].message_id == "blind"


def test_catalog_refuses_to_sort_by_an_unlisted_column(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_flow(archive)
        with pytest.raises(ValueError, match="direction"):
            archive.catalog(order_by="direction")


def test_catalog_paging_is_stable_under_a_tied_sort_key(tmp_path: Path) -> None:
    """Without the message_id tiebreak, ties duplicate and skip across pages."""
    with Archive.open(tmp_path / "state.db") as archive:
        for n in range(6):
            archive.record(
                _entry(f"m{n}", f"p{n}", direction="received", partner_name="SAME SRL")
            )
        pages = [
            e.message_id
            for offset in (0, 2, 4)
            for e in archive.catalog(order_by="partner_name", limit=2, offset=offset)
        ]
        assert sorted(pages) == [f"m{n}" for n in range(6)]


def test_catalog_filters_by_upload_date_including_the_whole_last_day(
    tmp_path: Path,
) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        archive.record(
            _entry(
                "late",
                "p-late",
                direction="received",
                created_at=dt.datetime(2026, 7, 20, 23, 59),
            )
        )
        archive.record(
            _entry(
                "early",
                "p-early",
                direction="received",
                created_at=dt.datetime(2026, 7, 19, 6, 0),
            )
        )
        window = CatalogQuery(
            uploaded_from=dt.date(2026, 7, 20), uploaded_to=dt.date(2026, 7, 20)
        )
        # A bare "created_at <= '2026-07-20'" would drop the 23:59 upload.
        assert [e.message_id for e in archive.catalog(window)] == ["late"]


def test_catalog_filters_number_and_partner_separately(tmp_path: Path) -> None:
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_catalog(archive)
        # Search spans both columns; the column filters do not.
        assert archive.catalog_count(CatalogQuery(number="ACME")) == 0
        assert archive.catalog_count(CatalogQuery(partner="ACME")) == 1
        assert archive.catalog_count(CatalogQuery(search="ACME")) == 1


@pytest.mark.parametrize("descending", [True, False])
def test_role_cifs_match_the_sql(tmp_path: Path, descending: bool) -> None:
    """The rendered roles and the sorted ones are the same rule, stated twice."""
    with Archive.open(tmp_path / "state.db") as archive:
        _seed_flow(archive)
        for key, index in (("from_cif", 0), ("to_cif", 1)):
            ordered = archive.catalog(order_by=key, descending=descending)
            values = [role_cifs(e)[index] for e in ordered]
            present = [v for v in values if v is not None]
            assert present == sorted(present, reverse=descending)
            # Blanks land last whichever way the sort runs.
            assert values[len(present) :] == [None] * (len(values) - len(present))
