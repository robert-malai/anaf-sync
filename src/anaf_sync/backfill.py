"""Index a folder of already-downloaded invoices into the archive catalog.

ANAF lists a message for 60 days and never again, so an operator adopting
anaf-sync has years of invoices it is structurally incapable of ever seeing —
and an archive whose database is lost cannot be rebuilt from the API. Both are
the same job: read the ZIPs off disk, parse them, and catalog what they say.

**Backfilled rows never gate a download.** ``Archive.is_archived`` answers "was
this ANAF message id ever archived", and a document read off disk carries no
message id: the ZIP's member names hold ANAF's *upload* index, which is shared
by the sender's and the receiver's copies of one invoice
(``efactura/api.md`` §3), and the UBL itself carries only issuer-controlled
identity. Keying the gate on either would risk skipping a real message — and
past 60 days a skipped message is lost for good, while a duplicate costs one
file. So these rows are catalog-only, and :mod:`engine` is untouched.

Identity is therefore synthetic: ``backfill:<sha256(content)[:16]>``, which
makes a re-run over the same folder update its rows instead of doubling them.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import structlog
from anafpy.efactura import DownloadedMessage
from pydantic import BaseModel, Field

from .config import ARTIFACT_SUFFIXES, SyncConfig
from .context import own_side, project_document, read_view
from .state import Archive, CatalogEntry
from .template import artifact_path

__all__ = ["BackfillReport", "run_backfill"]

logger = structlog.get_logger(__name__)


class BackfillReport(BaseModel):
    """Outcome of one backfill pass, for the CLI summary and the exit code."""

    scanned: int = 0
    indexed: int = 0
    #: Already in the catalog under this path — a synced row, or a prior run.
    already_known: int = 0
    #: Not a UBL invoice at all (a ``nok`` errors file, a buyer message).
    not_ubl: int = 0
    #: Parseable UBL that anafpy's reader refused — the vendored CIUS-RO edition
    #: has drifted. Counted apart from :attr:`not_ubl` because the two call for
    #: opposite responses: one is a file that was never an invoice, the other an
    #: invoice this build cannot read, and reporting them as one hides the bug.
    unreadable: int = 0
    #: Neither party is a configured CIF — someone else's invoice.
    foreign: int = 0
    failures: list[tuple[str, str]] = Field(default_factory=list)  # (path, error)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_backfill(
    folder: Path,
    config: SyncConfig,
    state: Archive,
    *,
    dry_run: bool = False,
) -> BackfillReport:
    """Catalog every readable invoice ZIP under ``folder``.

    Reads only: the ZIPs stay where they are, and nothing is downloaded,
    renamed, or moved. ``folder`` may be the configured output directory (to
    rebuild a lost catalog) or any other folder holding ANAF downloads.

    Raises:
        FileNotFoundError: ``folder`` does not exist.
    """
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a folder")
    report = BackfillReport()
    log = logger.bind(folder=str(folder))
    log.info("backfill_scanning")

    for path in sorted(folder.rglob("*.zip")):
        report.scanned += 1
        try:
            _index_one(path, config, state, report, dry_run=dry_run)
        except (zipfile.BadZipFile, OSError) as exc:
            # A file that cannot be read is reported, never fatal: one corrupt
            # ZIP must not abandon the rest of the folder.
            log.error("backfill_unreadable", path=str(path), error=str(exc))
            report.failures.append((str(path), str(exc)))

    log.info(
        "backfill_done",
        scanned=report.scanned,
        indexed=report.indexed,
        already_known=report.already_known,
        unreadable=report.unreadable,
    )
    return report


def _index_one(
    path: Path,
    config: SyncConfig,
    state: Archive,
    report: BackfillReport,
    *,
    dry_run: bool,
) -> None:
    """Catalog one ZIP, or count why it was passed over."""
    # The glob guarantees the ".zip" tail; slice it off rather than use
    # `with_suffix("")`, which would also eat a legal form's final segment
    # ("… S.R.L.zip" -> "… S.R"). See `template.artifact_path`.
    base = path.with_name(path.name[: -len(".zip")])

    message = DownloadedMessage.from_zip(path.read_bytes())
    view = read_view(message)
    if view is None or message.content_xml is None:
        if (error := message.view_error) is not None:
            report.unreadable += 1
            logger.warning("view_unreadable", path=str(path), error=str(error))
        else:
            report.not_ubl += 1
        return
    side = own_side(view, config.cifs)
    if side is None:
        report.foreign += 1
        return
    direction, cif = side

    digest = hashlib.sha256(message.content_xml).hexdigest()[:16]
    message_id = f"backfill:{digest}"
    # `claim_base` returns a suffixed path when the base belongs to a *different*
    # message — i.e. a synced row already catalogs these files. Leave it alone;
    # its ANAF message id is better than anything derivable here. An unowned
    # base, or this same document seen on a previous run, comes back unchanged.
    if state.claim_base(base, message_id) != base:
        report.already_known += 1
        return

    projection = project_document(view, cif=cif, direction=direction)
    entry = CatalogEntry(
        message_id=message_id,
        cif=cif,
        direction=direction,
        base_path=base.as_posix(),
        artifacts=_artifacts_on_disk(base),
        source="backfill",
        **projection.catalog,
    )
    report.indexed += 1
    if dry_run:
        logger.info("would_index", path=str(path), number=entry.number)
        return
    state.record(entry)
    logger.info(
        "indexed", path=str(path), number=entry.number, partner=entry.partner_name
    )


def _artifacts_on_disk(base: Path) -> list[str]:
    """Which artifacts actually sit next to ``base`` — what is, not what was
    configured, matching how the engine records them."""
    return [
        artifact.value
        for artifact, suffix in ARTIFACT_SUFFIXES.items()
        if artifact_path(base, suffix).exists()
    ]
