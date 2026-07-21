"""The Facturi table model over the archive catalog — logic, thinly Qt-wrapped.

``CatalogModel`` maps :meth:`anaf_sync.state.Archive.catalog` onto a
``QAbstractTableModel``: SQL-side filtering (search / direction / period),
``fetchMore`` paging for continuous scroll (no pagination UI), and the failing
messages from :attr:`Archive.failures` synthesised as pinned rows above the
catalog. Delayed and failing states are exposed as custom item roles for the
delegate to paint; the derivation itself stays in :mod:`anaf_sync.health`.
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

from ..health import (
    DELAY_THRESHOLD_DAYS,
    days_until_purge,
    upload_delay_days,
)
from ..state import Archive, CatalogEntry, FailureRecord
from .format import EM_DASH, money, short_date

__all__ = ["CatalogFilters", "CatalogModel", "FailureRow"]

#: Qt hands item-model methods either index flavour; accept both.
_Index = QModelIndex | QPersistentModelIndex

logger = structlog.get_logger(__name__)

_PAGE = 100
#: Upper bound on the client-side scans (problems view, problem count); a busy
#: archive with more delayed/failing rows than this logs a truncation notice
#: rather than silently under-reporting.
_SCAN_CAP = 5000


@dataclasses.dataclass(frozen=True)
class CatalogFilters:
    """The combinable filters behind the toolbar chips, period, and search."""

    search: str | None = None
    direction: str | None = None  # "received" | "sent" | None
    issued_from: dt.date | None = None
    issued_to: dt.date | None = None
    problems_only: bool = False


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
    DateRole = int(Qt.ItemDataRole.UserRole) + 5

    _COLUMNS = ("Data", "Număr", "Partener", "Direcție", "Total")

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
        self._failing: list[FailureRow] = []
        self._rows: list[CatalogEntry] = []
        self._total = 0
        self.reload()

    # -- public API -----------------------------------------------------------

    def set_filters(self, filters: CatalogFilters) -> None:
        self._filters = filters
        self.reload()

    def reload(self) -> None:
        """Re-read failing + first page from disk; resets the model."""
        self.beginResetModel()
        self._load()
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

    def problem_count(self) -> int:
        """Failing + delayed messages across the whole archive (for the chip)."""
        if not self._state_path.exists():
            return 0
        with Archive.open_readonly(self._state_path) as archive:
            failing = len(archive.failures)
            delayed = sum(1 for e in self._scan(archive) if _is_delayed(e))
        return failing + delayed

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
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return self._COLUMNS[section]
        return None

    def data(self, index: _Index, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid():
            return None
        record = self.entry(index.row())
        if isinstance(record, FailureRow):
            return self._failing_data(record, index.column(), role)
        return self._catalog_data(record, index.column(), role)

    def canFetchMore(self, parent: _Index) -> bool:
        if parent.isValid() or self._filters.problems_only:
            return False
        return len(self._rows) < self._total

    def fetchMore(self, parent: _Index) -> None:
        if not self.canFetchMore(parent):
            return
        with Archive.open_readonly(self._state_path) as archive:
            page = archive.catalog(
                **self._query_kwargs(), limit=_PAGE, offset=len(self._rows)
            )
        if not page:
            return
        start = self.shown_count()
        self.beginInsertRows(QModelIndex(), start, start + len(page) - 1)
        self._rows.extend(page)
        self.endInsertRows()

    # -- loading --------------------------------------------------------------

    def _load(self) -> None:
        if not self._state_path.exists():
            self._failing, self._rows, self._total = [], [], 0
            return
        with Archive.open_readonly(self._state_path) as archive:
            self._failing = self._build_failing(archive)
            if self._filters.problems_only:
                self._rows = [e for e in self._scan(archive) if _is_delayed(e)]
                self._total = len(self._rows)
            else:
                self._rows = archive.catalog(
                    **self._query_kwargs(), limit=_PAGE, offset=0
                )
                self._total = archive.catalog_count(**self._query_kwargs())

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
        # Failing rows carry no number/date/direction to match on, so they only
        # pin in the unfiltered list (or the problems view).
        if self._filters.problems_only:
            return True
        return self._filters.direction is None and not self._filters.search

    def _query_kwargs(self) -> dict[str, Any]:
        return {
            "search": self._filters.search,
            "direction": self._filters.direction,
            "issued_from": self._filters.issued_from,
            "issued_to": self._filters.issued_to,
        }

    def _scan(self, archive: Archive) -> list[CatalogEntry]:
        entries = archive.catalog(limit=_SCAN_CAP, offset=0)
        if len(entries) == _SCAN_CAP:
            logger.warning("problem_scan_truncated", cap=_SCAN_CAP)
        return entries

    # -- per-row rendering ----------------------------------------------------

    def _catalog_data(self, entry: CatalogEntry, col: int, role: int) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                short_date(entry.issue_date),
                entry.number or EM_DASH,
                entry.partner_name or EM_DASH,
                "",  # direction is painted as a pill by the delegate
                money(entry.total, entry.currency),
            )[col]
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 4:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == self.DirectionRole:
            return entry.direction
        if role == self.FailingRole:
            return False
        if role == self.DelayedRole:
            return _is_delayed(entry)
        if role == self.MessageIdRole:
            return entry.message_id
        if role == self.DateRole:
            return entry.issue_date
        return None

    def _failing_data(self, row: FailureRow, col: int, role: int) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                short_date(row.record.first_failed_at.date()),
                EM_DASH,
                EM_DASH,  # partner unknown until the message downloads
                "",
                EM_DASH,
            )[col]
        if role == self.DirectionRole:
            return "failing"
        if role == self.FailingRole:
            return True
        if role == self.DelayedRole:
            return False
        if role == self.MessageIdRole:
            return row.message_id
        if role == self.DateRole:
            return row.record.first_failed_at.date()
        return None


def _is_delayed(entry: CatalogEntry) -> bool:
    delay = upload_delay_days(entry.issue_date, entry.created_at)
    return delay is not None and delay > DELAY_THRESHOLD_DAYS
