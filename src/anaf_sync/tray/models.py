"""The Facturi table model over the archive catalog — logic, thinly Qt-wrapped.

``CatalogModel`` maps :meth:`anaf_sync.state.Archive.catalog` onto a
``QAbstractTableModel``: SQL-side filtering *and ordering*, ``fetchMore`` paging
for continuous scroll (no pagination UI), and the failing messages from
:attr:`Archive.failures` synthesised as pinned rows above the catalog. Delayed
and failing states are exposed as custom item roles for the delegate to paint;
the derivation itself stays in :mod:`anaf_sync.health`.

Sorting is deliberately *not* a proxy model. The rows arrive one page at a
time, so a ``QSortFilterProxyModel`` would order only what has been fetched and
silently re-shuffle the list as the reader scrolls; :meth:`CatalogModel.sort`
instead re-runs the query with a new ``ORDER BY`` and starts again from the top.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog
from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)

from ..health import days_until_purge, is_delayed
from ..state import (
    DEFAULT_ORDER_BY,
    Archive,
    CatalogEntry,
    CatalogQuery,
    FailureRecord,
    role_cifs,
)
from .format import EM_DASH, money, short_date

__all__ = [
    "ALL_DIRECTIONS",
    "COLUMN_SORT_KEYS",
    "COL_DIRECTION",
    "COL_FROM_CIF",
    "COL_ISSUED",
    "COL_NUMBER",
    "COL_PARTNER",
    "COL_TO_CIF",
    "COL_TOTAL",
    "COL_UPLOADED",
    "FAILING",
    "REAL_DIRECTIONS",
    "CatalogFilters",
    "CatalogModel",
    "FailureRow",
    "split_direction_choice",
]

#: The two values ``messages.direction`` actually holds.
REAL_DIRECTIONS = frozenset({"received", "sent"})
#: The synthetic third value the pinned failure rows carry. It names the
#: ``failures`` table, not a direction, so it never reaches a WHERE clause.
FAILING = "failing"
#: Everything the Direcție filter offers — its unfiltered state.
ALL_DIRECTIONS = REAL_DIRECTIONS | {FAILING}

#: Column indexes, named once so the delegate, the header and the model all
#: agree without counting tuple positions.
COL_ISSUED = 0
COL_UPLOADED = 1
COL_NUMBER = 2
COL_PARTNER = 3
COL_FROM_CIF = 4
COL_TO_CIF = 5
COL_DIRECTION = 6
COL_TOTAL = 7

#: Column index → the :meth:`Archive.catalog` sort key it stands for. Direcție
#: is the one omission, and deliberate: three values make a filter, not a sort.
COLUMN_SORT_KEYS: dict[int, str] = {
    COL_ISSUED: "issue_date",
    COL_UPLOADED: "created_at",
    COL_NUMBER: "number",
    COL_PARTNER: "partner_name",
    COL_FROM_CIF: "from_cif",
    COL_TO_CIF: "to_cif",
    COL_TOTAL: "total",
}
_SORT_COLUMNS = {key: col for col, key in COLUMN_SORT_KEYS.items()}

#: Qt hands item-model methods either index flavour; accept both.
_Index = QModelIndex | QPersistentModelIndex

logger = structlog.get_logger(__name__)

_PAGE = 100
#: Upper bound on the client-side scan the "doar întârziate" filter needs; a
#: busy archive with more matching rows than this logs a truncation notice
#: rather than silently under-reporting.
_SCAN_CAP = 5000


def split_direction_choice(
    chosen: frozenset[str],
) -> tuple[frozenset[str] | None, bool]:
    """Split the Direcție checklist into its SQL half and its failing flag.

    ``"failing"`` sits in the same checklist as the two real directions because
    that is where a reader looks for it, but it names the ``failures`` table —
    which has no row in ``messages`` to filter — so it must not reach a WHERE
    clause. Returns ``(directions, show_failing)``, where ``directions`` is
    ``None`` when both real directions are checked, i.e. unfiltered.
    """
    real = chosen & REAL_DIRECTIONS
    return (None if real == REAL_DIRECTIONS else real), FAILING in chosen


@dataclasses.dataclass(frozen=True)
class CatalogFilters:
    """What the header's filters add up to: a query, plus what SQL cannot ask.

    The two flags beside :attr:`query` are exactly the two questions the
    ``messages`` table cannot answer on its own — whether an invoice was
    declared late (derived from two dates per row, so a scan) and whether the
    failing messages, which live in another table entirely, are wanted.
    """

    query: CatalogQuery = CatalogQuery()
    delayed_only: bool = False
    show_failing: bool = True


@dataclasses.dataclass(frozen=True)
class FailureRow:
    """A pinned, synthesised row for a message that keeps failing to download."""

    message_id: str
    record: FailureRecord
    days_left: int


class CatalogModel(QAbstractTableModel):
    """A paged, filtered view of archived invoices with pinned failing rows."""

    FailingRole = int(Qt.ItemDataRole.UserRole) + 1
    DelayedRole = int(Qt.ItemDataRole.UserRole) + 2
    MessageIdRole = int(Qt.ItemDataRole.UserRole) + 3
    DirectionRole = int(Qt.ItemDataRole.UserRole) + 4
    #: True on whichever of the two CIF cells holds the *followed* CIF, so the
    #: delegate can paint your side of the flow at full strength.
    OwnCifRole = int(Qt.ItemDataRole.UserRole) + 5

    #: Emisă = issue date; Încărcată = SPV upload (``created_at``, em-dash on
    #: backfilled rows). "De la"/"Pentru" are the invoice's issuer and
    #: recipient — roles, not sides — so which one holds the followed CIF
    #: swaps with ``direction`` (:func:`anaf_sync.state.role_cifs`).
    _COLUMNS = (
        "Emisă",
        "Încărcată",
        "Număr",
        "Partener",
        "De la CIF",
        "Pentru CIF",
        "Direcție",
        "Total",
    )

    def __init__(
        self,
        state_path: Path,
        *,
        now: Callable[[], dt.datetime] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._state_path = state_path
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._filters = CatalogFilters()
        self._order_by = DEFAULT_ORDER_BY
        self._descending = True
        self._failing: list[FailureRow] = []
        self._rows: list[CatalogEntry] = []
        self._total = 0
        self.reload()

    # -- public API -----------------------------------------------------------

    def set_filters(self, filters: CatalogFilters) -> None:
        self._filters = filters
        self._reset(_PAGE)  # a new filter is a new list; start at the top

    def reload(self) -> None:
        """Re-read from disk without losing paged depth; resets the model.

        Refetches at least as many rows as were already loaded, so a refresh
        mid-scroll (a sync commit landing, a config or theme change) does not
        collapse the catalog back to the first page under the reader.
        """
        self._reset(max(_PAGE, len(self._rows)))

    def sort(  # noqa: N802 — Qt override
        self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
    ) -> None:
        """Re-run the query under a new ``ORDER BY``; ignore unsortable columns.

        Qt calls this for any header click once sorting is enabled, including
        on Direcție — which has no sort key. Returning quietly is right: the
        header refuses that click before it gets here, and a model that raised
        would turn a mis-wired view into a crash.
        """
        key = COLUMN_SORT_KEYS.get(column)
        if key is None:
            return
        self._order_by = key
        self._descending = order == Qt.SortOrder.DescendingOrder
        self._reset(_PAGE)  # a new order is a new list; start at the top

    @property
    def sort_column(self) -> int:
        """The column index the catalog is currently ordered by."""
        return _SORT_COLUMNS[self._order_by]

    @property
    def sort_order(self) -> Qt.SortOrder:
        return (
            Qt.SortOrder.DescendingOrder
            if self._descending
            else Qt.SortOrder.AscendingOrder
        )

    def row_of(self, message_id: str) -> int | None:
        """The current row of ``message_id`` among the loaded rows, if any."""
        for row in range(self.shown_count()):
            if self.entry(row).message_id == message_id:
                return row
        return None

    def _reset(self, limit: int) -> None:
        self.beginResetModel()
        self._load(limit)
        self.endResetModel()

    def entry(self, row: int) -> CatalogEntry | FailureRow:
        """The underlying record for a row (a catalog entry or a failing row)."""
        if row < len(self._failing):
            return self._failing[row]
        return self._rows[row - len(self._failing)]

    def shown_count(self) -> int:
        return len(self._failing) + len(self._rows)

    def total_count(self) -> int:
        """Archived-message total for the current filters (excludes failing)."""
        return self._total

    # -- QAbstractTableModel --------------------------------------------------

    def rowCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else self.shown_count()

    def columnCount(self, parent: _Index = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            # Qt centres section labels by default, which reads as a caption
            # over a wide column rather than a heading for it. Each label sits
            # over its own values instead.
            return int(
                (
                    Qt.AlignmentFlag.AlignRight
                    if section == COL_TOTAL
                    else Qt.AlignmentFlag.AlignLeft
                )
                | Qt.AlignmentFlag.AlignVCenter
            )
        return None

    def data(self, index: _Index, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid():
            return None
        record = self.entry(index.row())
        if isinstance(record, FailureRow):
            return self._failing_data(record, index.column(), role)
        return self._catalog_data(record, index.column(), role)

    def canFetchMore(self, parent: _Index) -> bool:
        # The delayed filter is derived per row, so its result set is already
        # whole — there are no further pages to ask SQL for.
        if parent.isValid() or self._filters.delayed_only:
            return False
        return len(self._rows) < self._total

    def fetchMore(self, parent: _Index) -> None:
        if not self.canFetchMore(parent):
            return
        with Archive.open_readonly(self._state_path) as archive:
            page = archive.catalog(
                self._filters.query,
                order_by=self._order_by,
                descending=self._descending,
                limit=_PAGE,
                offset=len(self._rows),
            )
        if not page:
            return
        start = self.shown_count()
        self.beginInsertRows(QModelIndex(), start, start + len(page) - 1)
        self._rows.extend(page)
        self.endInsertRows()

    # -- loading --------------------------------------------------------------

    def _load(self, limit: int) -> None:
        if not self._state_path.exists():
            self._failing, self._rows, self._total = [], [], 0
            return
        with Archive.open_readonly(self._state_path) as archive:
            self._failing = self._build_failing(archive)
            if self._filters.delayed_only:
                # Lateness is two dates compared per row, not a column, so this
                # one filter cannot be a WHERE clause and pays for a scan.
                self._rows = [e for e in self._scan(archive) if _is_delayed(e)]
                self._total = len(self._rows)
            else:
                self._rows = archive.catalog(
                    self._filters.query,
                    order_by=self._order_by,
                    descending=self._descending,
                    limit=limit,
                    offset=0,
                )
                self._total = archive.catalog_count(self._filters.query)

    def _build_failing(self, archive: Archive) -> list[FailureRow]:
        if not self._show_failing():
            return []
        now = self._now()
        rows = [
            FailureRow(mid, rec, days_until_purge(rec, now))
            for mid, rec in archive.failures.items()
        ]
        rows.sort(key=lambda r: r.days_left)  # most urgent first
        return rows

    def _show_failing(self) -> bool:
        """Whether the pinned failure rows belong in the current result set.

        A failing message has no number, partner, CIF, issue date or upload
        date — nothing was downloaded yet. Any filter naming one of those is
        asking it a question it cannot answer, and pinning it anyway would
        claim it matched. Only the Direcție checklist and the delayed flag can
        speak for it, and the flag never can.
        """
        if not self._filters.show_failing or self._filters.delayed_only:
            return False
        q = self._filters.query
        return not any(
            (
                q.search,
                q.number,
                q.partner,
                q.from_cif,
                q.to_cif,
                q.issued_from,
                q.issued_to,
                q.uploaded_from,
                q.uploaded_to,
            )
        )

    def _scan(self, archive: Archive) -> list[CatalogEntry]:
        entries = archive.catalog(
            self._filters.query,
            order_by=self._order_by,
            descending=self._descending,
            limit=_SCAN_CAP,
            offset=0,
        )
        if len(entries) == _SCAN_CAP:
            logger.warning("delayed_scan_truncated", cap=_SCAN_CAP)
        return entries

    # -- per-row rendering ----------------------------------------------------

    def _catalog_data(self, entry: CatalogEntry, col: int, role: int) -> Any:
        issuer, recipient = role_cifs(entry)
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                short_date(entry.issue_date),
                short_date(entry.created_at.date() if entry.created_at else None),
                entry.number or EM_DASH,
                entry.partner_name or EM_DASH,
                issuer or EM_DASH,
                recipient or EM_DASH,
                "",  # direction is painted as a pill by the delegate
                money(entry.total, entry.currency),
            )[col]
        if role == Qt.ItemDataRole.TextAlignmentRole and col == COL_TOTAL:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == self.OwnCifRole:
            # Exactly one of the two role columns is you, and which one it is
            # is the direction restated — see :func:`state.role_cifs`.
            return (col == COL_FROM_CIF and entry.direction == "sent") or (
                col == COL_TO_CIF and entry.direction != "sent"
            )
        if role == self.DirectionRole:
            return entry.direction
        if role == self.FailingRole:
            return False
        if role == self.DelayedRole:
            return _is_delayed(entry)
        if role == self.MessageIdRole:
            return entry.message_id
        return None

    def _failing_data(self, row: FailureRow, col: int, role: int) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            # Only the first-failure date is known: nothing downloaded, so the
            # upload date, partner, both CIFs and the total have no value yet.
            # The failures table does not even record whose inbox it was.
            return (
                short_date(row.record.first_failed_at.date()),
                EM_DASH,
                EM_DASH,
                EM_DASH,
                EM_DASH,
                EM_DASH,
                "",
                EM_DASH,
            )[col]
        if role == self.DirectionRole:
            return FAILING
        if role == self.FailingRole:
            return True
        if role in (self.DelayedRole, self.OwnCifRole):
            return False
        if role == self.MessageIdRole:
            return row.message_id
        return None


def _is_delayed(entry: CatalogEntry) -> bool:
    return is_delayed(entry.issue_date, entry.created_at)
