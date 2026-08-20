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
from collections.abc import Collection
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEFAULT_ORDER_BY",
    "SORTABLE_COLUMNS",
    "Archive",
    "CatalogEntry",
    "CatalogQuery",
    "FailureRecord",
    "RunRecord",
    "role_cifs",
]

#: The two role CIFs are derived, not stored: an invoice goes *from* one CIF
#: *to* another, and which of them is the followed one depends on
#: ``direction``. Kept as SQL rather than computed per page in the reader, so
#: that ordering and filtering on them stay server-side and paged.
_FROM_CIF = "CASE direction WHEN 'sent' THEN cif ELSE partner_cif END"
_TO_CIF = "CASE direction WHEN 'sent' THEN partner_cif ELSE cif END"


def role_cifs(entry: CatalogEntry) -> tuple[str | None, str | None]:
    """``(issuer, recipient)`` for one row — the Python mirror of the SQL above.

    Readers render these two columns while SQL sorts and filters them, so the
    rule is stated twice and could drift; ``test_role_cifs_match_the_sql``
    pins the two statements together.
    """
    if entry.direction == "sent":
        return entry.cif, entry.partner_cif
    return entry.partner_cif, entry.cif


#: Sortable columns mapped to the SQL that orders them. This is a whitelist,
#: not a format hole: the value is spliced straight into ``ORDER BY``, so
#: nothing that is not a key here may ever reach it. ``direction`` is
#: deliberately absent — three values make a filter, not a sort.
_SORT_EXPR: dict[str, str] = {
    "issue_date": "issue_date",
    "created_at": "created_at",
    "number": "number",
    "partner_name": "partner_name",
    "from_cif": _FROM_CIF,
    "to_cif": _TO_CIF,
    "total": "total",
}

#: What :meth:`Archive.catalog` will accept as ``order_by``.
SORTABLE_COLUMNS = frozenset(_SORT_EXPR)

#: The order the catalog has always emitted, and still does unasked.
DEFAULT_ORDER_BY = "issue_date"

_SCHEMA_VERSION = "3"

_SCHEMA = f"""
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
    created_at   TEXT,
    source       TEXT NOT NULL DEFAULT 'sync'
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
INSERT INTO meta (key, value) VALUES ('schema_version', '{_SCHEMA_VERSION}');
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
    #: How the row got here. ``"backfill"`` rows were read off disk, not
    #: downloaded: their ``message_id`` is synthetic and ``created_at`` is
    #: always ``None``, so a reader must not take "no delay" from it — see
    #: ``health.is_delayed``, which cannot tell absent from on-time.
    source: Literal["sync", "backfill"] = "sync"


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
    #: Messages ANAF listed but refused to hand over, their 60-day download
    #: window having shut. Not failures — no trace of them is kept anywhere
    #: else, so this count is the only record that the run met any. Routine on
    #: a first sync; on a later one it means the schedule had a gap wide enough
    #: to lose invoices. Defaulted, so records written before it existed (and
    #: the ``failed`` paths, which have no report) still parse.
    expired: int = 0
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


class CatalogQuery(BaseModel):
    """The SQL-side filters :meth:`Archive.catalog` understands, as one value.

    Grouped rather than spread across keyword arguments: every filter otherwise
    has to be threaded by hand through :meth:`Archive.catalog`,
    :meth:`Archive.catalog_count` and their shared WHERE builder, and three
    signatures that must agree drift the moment one of them is edited alone.

    Every field is "unset means unfiltered". ``search`` spans two columns
    (``number`` OR ``partner_name``); the rest each name one. ``from_cif`` and
    ``to_cif`` filter the *derived* role CIFs — issuer and recipient — not the
    stored ``cif``/``partner_cif`` pair, so they read the same for a sent
    invoice as for a received one.
    """

    model_config = ConfigDict(frozen=True)

    search: str | None = None
    number: str | None = None
    partner: str | None = None
    from_cif: str | None = None
    to_cif: str | None = None
    #: ``None`` means every direction; an empty set means none, and matches
    #: nothing rather than raising — a UI mid-edit should not crash the reader.
    directions: frozenset[str] | None = None
    issued_from: dt.date | None = None
    issued_to: dt.date | None = None
    #: Compared against ``created_at``'s *date*, so an upper bound includes
    #: everything uploaded during that day rather than only midnight.
    uploaded_from: dt.date | None = None
    uploaded_to: dt.date | None = None


#: Shared default for the query parameter — immutable, so one instance is safe.
_NO_FILTERS = CatalogQuery()


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

        The registry key is the POSIX form (``as_posix``) so it stays canonical
        across platforms; callers that persist ``base_path`` must store the same.
        """
        row = self._conn.execute(
            "SELECT message_id FROM messages WHERE base_path = ?", (base.as_posix(),)
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
                    currency, message_type, created_at, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    created_at   = excluded.created_at,
                    source       = excluded.source
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
                    entry.source,
                ),
            )
            self._conn.execute(
                "DELETE FROM failures WHERE message_id = ?", (entry.message_id,)
            )

    def missing_pdf(self) -> list[CatalogEntry]:
        """Synced rows whose artifacts lack a PDF — the repair pass's worklist.

        Backfill rows are excluded by construction: they catalog folders the
        engine does not own, and writing new files into them is not this tool's
        call. The dedupe gate is untouched — this reads what a row already says
        about itself, it never changes what ``is_archived`` answers.
        """
        rows = self._conn.execute(
            "SELECT * FROM messages"
            " WHERE source = 'sync' AND artifacts NOT LIKE '%\"pdf\"%'"
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def synced(
        self, *, message_ids: Collection[str] | None = None
    ) -> list[CatalogEntry]:
        """Every downloaded row — the reprocess pass's worklist.

        Backfill rows are excluded for the same reason :meth:`missing_pdf`
        excludes them: they catalog folders the engine does not own, so
        re-deriving their paths and moving their files is not this tool's call.
        Ordered by ``base_path`` so a pass that relocates files walks the
        archive folder by folder rather than criss-crossing it.

        ``message_ids`` narrows the worklist to those messages — one row is what
        the tray's per-invoice button asks for, and filtering in SQL keeps that
        a single-row read on an archive of any size. Ids that are absent (or
        name a backfill row) simply do not come back; the caller decides whether
        that is worth reporting.
        """
        if message_ids is None:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE source = 'sync' ORDER BY base_path"
            ).fetchall()
        elif not message_ids:
            return []
        else:
            slots = ", ".join("?" * len(message_ids))
            rows = self._conn.execute(
                f"SELECT * FROM messages WHERE source = 'sync' "
                f"AND message_id IN ({slots}) ORDER BY base_path",
                tuple(message_ids),
            ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def reproject(self, entry: CatalogEntry) -> None:
        """Rewrite one row from a re-derived projection; ``saved_at`` is kept.

        Everything :meth:`record` writes except that stamp: nothing was
        downloaded, so the row must go on naming the run that archived it — and
        for the same reason no failure trace is cleared, since this pass never
        asked ANAF anything. ``entry`` is the row's intended full state, merge
        included: the caller decides what a re-projection may overwrite and
        what only the (long gone) listing entry could have known.

        An unknown ``message_id`` is a no-op, so an interrupted pass re-runs.
        """
        with self._conn:
            self._conn.execute(
                """
                UPDATE messages SET
                    cif          = ?,
                    direction    = ?,
                    base_path    = ?,
                    artifacts    = ?,
                    issue_date   = ?,
                    number       = ?,
                    partner_name = ?,
                    partner_cif  = ?,
                    total        = ?,
                    currency     = ?,
                    message_type = ?,
                    created_at   = ?
                WHERE message_id = ?
                """,
                (
                    entry.cif,
                    entry.direction,
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
                    entry.message_id,
                ),
            )

    def add_artifact(self, message_id: str, artifact: str) -> None:
        """Append one artifact to a recorded message (a repaired PDF, say).

        Touches only ``artifacts`` — ``saved_at`` keeps naming the run that
        archived the message, not the repair. An unknown id or an
        already-present value is a no-op, so a crashed repair can simply run
        again.
        """
        row = self._conn.execute(
            "SELECT artifacts FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return
        artifacts = json.loads(row["artifacts"])
        if artifact in artifacts:
            return
        with self._conn:
            self._conn.execute(
                "UPDATE messages SET artifacts = ? WHERE message_id = ?",
                (json.dumps([*artifacts, artifact]), message_id),
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
        query: CatalogQuery = _NO_FILTERS,
        *,
        order_by: str = DEFAULT_ORDER_BY,
        descending: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CatalogEntry]:
        """A filtered, ordered, paged slice of the archived catalog.

        Defaults to newest-issued first, which is the order the catalog has
        always emitted. Filtering and ordering both happen in SQL so the caller
        can lazy-load pages: a reader that sorts the pages it has already
        fetched would silently re-order the list as the user scrolls.

        Args:
            query: The filters to apply; unset fields do not filter.
            order_by: A key of :data:`SORTABLE_COLUMNS`.
            descending: Sort direction. Rows whose sort value is ``NULL`` land
                last either way.
            limit: Page size.
            offset: Rows to skip before the page.

        Raises:
            ValueError: If ``order_by`` names a column that cannot be sorted.
        """
        where, params = _catalog_filters(query)
        rows = self._conn.execute(
            f"SELECT * FROM messages{where} {_order_clause(order_by, descending)} "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_entry_from_row(row) for row in rows]

    def catalog_count(self, query: CatalogQuery = _NO_FILTERS) -> int:
        """How many archived messages match the same filters as :meth:`catalog`."""
        where, params = _catalog_filters(query)
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM messages{where}", params
        ).fetchone()
        return int(row["n"])

    def distinct_cifs(self) -> list[str]:
        """Every CIF that appears in the archive, sorted — for the Settings UI.

        The tray offers these (unioned with the configured CIFs) as the
        follow-list choices, since anafpy exposes no authorized-CIF API.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT cif FROM messages ORDER BY cif"
        ).fetchall()
        return [row["cif"] for row in rows]

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
        # Applied in sequence, so a v1 archive reaches v3 in one open.
        if found == "1":
            self._migrate_v1_to_v2()
            found = "2"
        if found == "2":
            self._migrate_v2_to_v3()
            found = "3"
        if found == _SCHEMA_VERSION:
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

    def _migrate_v2_to_v3(self) -> None:
        """Add ``source``; every existing row is by definition a synced one.

        ``NOT NULL DEFAULT 'sync'`` backfills them in the ``ALTER`` itself, so
        the column can be read unconditionally — no ``NULL`` tier meaning
        "written before we tracked this".
        """
        with self._conn:
            self._conn.execute(
                "ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'sync'"
            )
            self._conn.execute(
                "UPDATE meta SET value = '3' WHERE key = 'schema_version'"
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


def _order_clause(order_by: str, descending: bool) -> str:
    """The ``ORDER BY`` for one sort key — whitelisted, blanks last, unique.

    Two details the caller must not have to remember. Rows with no value sort
    last in *both* directions, which is what the fixed order always did with
    ``issue_date IS NULL``; and ``message_id`` closes every ordering, because
    without a unique final key ``LIMIT``/``OFFSET`` paging over a non-unique
    sort column duplicates and skips rows between pages.
    """
    if order_by not in _SORT_EXPR:
        raise ValueError(f"cannot sort the catalog by {order_by!r}")
    expr = _SORT_EXPR[order_by]
    return (
        f"ORDER BY {expr} IS NULL, {expr} "
        f"{'DESC' if descending else 'ASC'}, message_id DESC"
    )


def _catalog_filters(query: CatalogQuery) -> tuple[str, list[object]]:
    """Build the ``WHERE`` fragment and its parameters for one query."""
    clauses: list[str] = []
    params: list[object] = []

    def contains(expr: str, needle: str | None) -> None:
        if needle:
            clauses.append(f"{expr} LIKE ?")
            params.append(f"%{needle}%")

    if query.search:
        clauses.append("(number LIKE ? OR partner_name LIKE ?)")
        params += [f"%{query.search}%"] * 2
    contains("number", query.number)
    contains("partner_name", query.partner)
    contains(_FROM_CIF, query.from_cif)
    contains(_TO_CIF, query.to_cif)
    if query.directions is not None:
        # "IN ()" is a syntax error, so an empty selection is spelled out as
        # the false constant rather than left to SQLite to reject.
        if query.directions:
            clauses.append(f"direction IN ({', '.join('?' * len(query.directions))})")
            params += sorted(query.directions)
        else:
            clauses.append("0")
    if query.issued_from is not None:
        clauses.append("issue_date >= ?")
        params.append(query.issued_from.isoformat())
    if query.issued_to is not None:
        clauses.append("issue_date <= ?")
        params.append(query.issued_to.isoformat())
    # created_at is a full timestamp; comparing its date is what makes an
    # upper bound mean "everything uploaded that day" rather than "by midnight".
    if query.uploaded_from is not None:
        clauses.append("date(created_at) >= ?")
        params.append(query.uploaded_from.isoformat())
    if query.uploaded_to is not None:
        clauses.append("date(created_at) <= ?")
        params.append(query.uploaded_to.isoformat())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


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
        source=row["source"],
    )
