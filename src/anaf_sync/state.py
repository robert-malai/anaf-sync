"""The archive database: dedupe gate, path registry, and permanent catalog.

A SQLite database (stdlib ``sqlite3``, no dependencies) with three jobs:

- **Dedupe gate.** Every run lists ANAF's full retention window; ``is_archived``
  decides what is new. A message id is recorded the moment its artifacts land,
  and kept *forever*: past ANAF's 60-day window a message can never be listed
  again, so the record can never cause a spurious skip — permanence is safe and
  makes the archive its own permanent catalog.
- **Path registry.** ``base_path`` is ``UNIQUE``; ``claim_base`` reads it to
  keep two invoices that render the same template path from clobbering each
  other.
- **Catalog.** Best-effort invoice fields (partner, date, number, total, …) are
  stored alongside each message so a future UI can browse the archive without
  re-parsing UBL.

Durability is the class's contract, not the caller's: every mutating method
commits its own transaction before returning, so a crash mid-run redoes at most
the in-flight message — harmless, because downloads are idempotent GETs. WAL
with ``synchronous=NORMAL`` can lose at most the last commit on power loss,
which costs one re-download next run, and lets a future UI read while a sync
writes.

Only failure traces are pruned (observability-only, they go stale); downloaded
records never are.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel

__all__ = ["Archive", "CatalogEntry", "FailureRecord", "RunRecord"]

_SCHEMA_VERSION = "2"

_SCHEMA = """
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
    message_type TEXT,
    created_at   TEXT
);
CREATE INDEX idx_messages_issue_date ON messages(issue_date);
CREATE INDEX idx_messages_partner    ON messages(partner_name);

CREATE TABLE failures (
    message_id      TEXT PRIMARY KEY,
    first_failed_at TEXT NOT NULL,
    last_failed_at  TEXT NOT NULL,
    attempts        INTEGER NOT NULL,
    error           TEXT NOT NULL
);

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO meta (key, value) VALUES ('schema_version', '2');
"""


class CatalogEntry(BaseModel):
    """Everything recorded about one archived message (write model)."""

    message_id: str
    cif: str
    direction: str  # from context.direction_of / DirectionLabel
    base_path: str
    artifacts: list[str]
    # Catalog tier: best-effort from the UBL view; None when unparseable.
    issue_date: dt.date | None = None
    number: str | None = None
    partner_name: str | None = None
    partner_cif: str | None = None
    total: float | None = None
    currency: str | None = None
    message_type: str | None = None
    #: ANAF's ``data_creare`` (when the message entered SPV), parsed by
    #: ``context._parse_created``. Nullable; needed by the delayed-invoice check.
    created_at: dt.datetime | None = None


class RunRecord(BaseModel):
    """The outcome of the most recent ``anaf-sync sync`` invocation.

    Written by the CLI on every exit path (success, caught boundary error, and
    the system-mode crash excepthook) so the desktop companion can tell a
    healthy schedule from a broken one without re-running anything. Stored as a
    single JSON blob under the ``meta`` key ``last_run``.
    """

    finished_at: dt.datetime
    outcome: Literal["ok", "failed", "crashed"]
    listed: int = 0
    archived: int = 0
    failures: int = 0
    #: One-line human summary of what went wrong (``None`` on success).
    error: str | None = None
    #: The exception class name (e.g. ``AnafAuthError``); drives the health
    #: state's auth/config error family. ``None`` for per-message failures.
    error_kind: str | None = None


class FailureRecord(BaseModel):
    """A message that keeps failing to download — kept for visibility only.

    Never gates retrying: the engine retries anything absent from the archive on
    every run regardless. This exists so ``anaf-sync status`` can surface a
    persistent failure before ANAF's 60-day window closes on it.
    """

    first_failed_at: dt.datetime
    last_failed_at: dt.datetime
    attempts: int = 1
    error: str


class Archive:
    """The archive database: dedupe gate, path registry, and catalog.

    Context manager; every mutating method commits its own transaction before
    returning (durability is the class's contract, not the caller's).
    """

    def __init__(self, path: Path, conn: sqlite3.Connection) -> None:
        self._path = path
        self._conn = conn

    @classmethod
    def open(cls, path: Path, *, failure_retention: dt.timedelta | None = None) -> Self:
        """Open the archive, creating the schema when the file is new.

        When ``failure_retention`` is given, prune failure traces whose last
        attempt is older than it — callers doing read-only or dry-run work omit
        it so state is untouched.

        Raises:
            ValueError: the existing database has an unsupported schema version.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        archive = cls(path, conn)
        archive._init_schema()
        if failure_retention is not None:
            archive._prune_failures(failure_retention)
        return archive

    @classmethod
    def open_readonly(cls, path: Path) -> Self:
        """Open the archive read-only — for observers that must never write.

        WAL (set by :meth:`open`) lets this connection query the catalog while a
        scheduled sync writes, with no schema init and no pruning. Intended for
        the desktop companion.

        Raises:
            FileNotFoundError: the database does not exist yet (no sync has run).
        """
        if not path.exists():
            raise FileNotFoundError(
                f"no archive at {path} — run `anaf-sync sync` first"
            )
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return cls(path, conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def is_archived(self, message_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row is not None

    def claim_base(self, base: Path, message_id: str) -> Path:
        """Avoid clobbering a different invoice that rendered the same path.

        A base recorded for a *different* message returns ``base`` with an
        ``_{message_id}`` suffix; anything else — unowned, or this message's own
        prior path on ``--redownload`` or after a crash before recording — is
        returned as-is to be overwritten in place, never duplicated.
        """
        row = self._conn.execute(
            "SELECT message_id FROM messages WHERE base_path = ?", (str(base),)
        ).fetchone()
        if row is not None and row["message_id"] != message_id:
            return base.with_name(f"{base.name}_{message_id}")
        return base

    def record(self, entry: CatalogEntry) -> None:
        """Upsert one archived message; stamps ``saved_at`` (UTC).

        Re-archiving a message at a new base path updates the same row, so its
        old path is released (the ``UNIQUE`` constraint on ``base_path`` is what
        makes a single ``UPDATE`` the right shape). Clears any failure trace.
        """
        saved_at = dt.datetime.now(dt.UTC).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO messages (
                    message_id, cif, direction, saved_at, base_path, artifacts,
                    issue_date, number, partner_name, partner_cif, total,
                    currency, message_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    cif          = excluded.cif,
                    direction    = excluded.direction,
                    saved_at     = excluded.saved_at,
                    base_path    = excluded.base_path,
                    artifacts    = excluded.artifacts,
                    issue_date   = excluded.issue_date,
                    number       = excluded.number,
                    partner_name = excluded.partner_name,
                    partner_cif  = excluded.partner_cif,
                    total        = excluded.total,
                    currency     = excluded.currency,
                    message_type = excluded.message_type,
                    created_at   = excluded.created_at
                """,
                (
                    entry.message_id,
                    entry.cif,
                    entry.direction,
                    saved_at,
                    entry.base_path,
                    json.dumps(entry.artifacts),
                    entry.issue_date.isoformat() if entry.issue_date else None,
                    entry.number,
                    entry.partner_name,
                    entry.partner_cif,
                    entry.total,
                    entry.currency,
                    entry.message_type,
                    entry.created_at.isoformat() if entry.created_at else None,
                ),
            )
            self._conn.execute(
                "DELETE FROM failures WHERE message_id = ?", (entry.message_id,)
            )

    def record_failure(self, message_id: str, error: str) -> None:
        """Insert a failure trace, or bump attempts/last_failed_at/error."""
        now = dt.datetime.now(dt.UTC).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO failures (
                    message_id, first_failed_at, last_failed_at, attempts, error
                ) VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    last_failed_at = excluded.last_failed_at,
                    attempts       = attempts + 1,
                    error          = excluded.error
                """,
                (message_id, now, now, error),
            )

    def record_run(self, run: RunRecord) -> None:
        """Persist the outcome of the most recent sync (see :class:`RunRecord`)."""
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_run', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (run.model_dump_json(),),
            )

    def last_run(self) -> RunRecord | None:
        """The last recorded sync outcome, or ``None`` before the first run."""
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'last_run'"
        ).fetchone()
        return RunRecord.model_validate_json(row["value"]) if row is not None else None

    def catalog(
        self,
        *,
        search: str | None = None,
        direction: str | None = None,
        issued_from: dt.date | None = None,
        issued_to: dt.date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CatalogEntry]:
        """A filtered, paged slice of the archived catalog.

        Ordered newest-first by ``issue_date`` then ``message_id`` (rows with no
        issue date sort last). Filtering happens in SQL so the caller can
        lazy-load pages instead of materialising the whole table. ``search``
        matches ``number`` or ``partner_name`` case-insensitively.
        """
        where, params = self._catalog_filters(search, direction, issued_from, issued_to)
        rows = self._conn.execute(
            f"SELECT * FROM messages{where} "
            "ORDER BY issue_date IS NULL, issue_date DESC, message_id DESC "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def catalog_count(
        self,
        *,
        search: str | None = None,
        direction: str | None = None,
        issued_from: dt.date | None = None,
        issued_to: dt.date | None = None,
    ) -> int:
        """How many archived messages match the same filters as :meth:`catalog`."""
        where, params = self._catalog_filters(search, direction, issued_from, issued_to)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM messages{where}", params
        ).fetchone()
        return int(row["n"])

    @staticmethod
    def _catalog_filters(
        search: str | None,
        direction: str | None,
        issued_from: dt.date | None,
        issued_to: dt.date | None,
    ) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if search:
            clauses.append("(number LIKE ? OR partner_name LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if direction:
            clauses.append("direction = ?")
            params.append(direction)
        if issued_from is not None:
            clauses.append("issue_date >= ?")
            params.append(issued_from.isoformat())
        if issued_to is not None:
            clauses.append("issue_date <= ?")
            params.append(issued_to.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    @property
    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()
        return int(row["n"])

    @property
    def failures(self) -> dict[str, FailureRecord]:
        rows = self._conn.execute(
            "SELECT message_id, first_failed_at, last_failed_at, attempts, error "
            "FROM failures"
        ).fetchall()
        return {
            row["message_id"]: FailureRecord(
                first_failed_at=row["first_failed_at"],
                last_failed_at=row["last_failed_at"],
                attempts=row["attempts"],
                error=row["error"],
            )
            for row in rows
        }

    @property
    def path(self) -> Path:
        return self._path

    def _init_schema(self) -> None:
        exists = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
        ).fetchone()
        if exists is None:
            self._conn.executescript(_SCHEMA)  # DDL script commits itself
            return
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        found = row["value"] if row is not None else None
        if found == _SCHEMA_VERSION:
            return
        if found == "1":
            self._migrate_v1_to_v2()
            return
        raise ValueError(
            f"archive at {self._path} has schema version {found!r}, "
            f"expected {_SCHEMA_VERSION!r} — delete it to start fresh"
        )

    def _migrate_v1_to_v2(self) -> None:
        """Add the nullable ``created_at`` column; existing rows keep NULL.

        Additive and in place — the archive is a permanent catalog, so its rows
        are never rebuilt, only extended.
        """
        with self._conn:
            self._conn.execute("ALTER TABLE messages ADD COLUMN created_at TEXT")
            self._conn.execute(
                "UPDATE meta SET value = '2' WHERE key = 'schema_version'"
            )

    def _prune_failures(self, max_age: dt.timedelta) -> None:
        """Drop failure traces whose last attempt is older than ``max_age``.

        Such messages have aged out of ANAF's listing window; their traces can
        no longer point at anything actionable. Downloaded records are never
        pruned — they are the permanent catalog.
        """
        cutoff = (dt.datetime.now(dt.UTC) - max_age).isoformat()
        with self._conn:
            self._conn.execute(
                "DELETE FROM failures WHERE last_failed_at < ?", (cutoff,)
            )


def _entry_from_row(row: sqlite3.Row) -> CatalogEntry:
    """Reconstruct a :class:`CatalogEntry` from a ``messages`` row."""
    return CatalogEntry(
        message_id=row["message_id"],
        cif=row["cif"],
        direction=row["direction"],
        base_path=row["base_path"],
        artifacts=json.loads(row["artifacts"]),
        issue_date=(
            dt.date.fromisoformat(row["issue_date"]) if row["issue_date"] else None
        ),
        number=row["number"],
        partner_name=row["partner_name"],
        partner_cif=row["partner_cif"],
        total=row["total"],
        currency=row["currency"],
        message_type=row["message_type"],
        created_at=(
            dt.datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
        ),
    )
