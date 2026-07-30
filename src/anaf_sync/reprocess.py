"""Re-derive an archived message's catalog fields — and its path — from disk.

A download can land with every XML-derived field collapsed to ``unknown``:
anafpy's reader could not represent a document ANAF accepted (rule drift, see
:func:`context.read_view`), so the invoice was archived under
``…/unknown/unknown_…`` and shows the same blanks in the tray. The archive has
no second chance at it through ANAF — the dedupe gate never revisits a message,
and past the 60-day window ``sync --redownload`` cannot reach it either.

The evidence is not lost, though. The invoice XML sits in the archived ZIP
forever, so the projection can simply be run again — offline, unconstrained by
those 60 days, and as many times as anafpy learns to read more. That is the
argument :func:`engine.repair_pdfs` already rests on, applied to the catalog and
the path instead of the PDF.

Two tiers, because they differ in blast radius:

- **Refreshing** rewrites the row's derived columns. Invisible from disk and
  worth doing unconditionally.
- **Moving** (``move=True``) re-renders the path template from the improved
  projection and relocates the message's files under it. This is what actually
  empties an ``unknown`` folder — and, by the same mechanism, what re-files the
  archive after a template change, so it is opt-in and honours ``dry_run``.

What is *not* re-derived is what only ANAF's listing carried: ``message_type``
and ``created_at`` (``data_creare``, which the delay flag reads) stay as the
download recorded them, as do ``cif``, ``direction``, ``saved_at`` and the
message id. ``request_id`` is the one path variable with no home in the archive
at all; a template referencing it refuses the whole pass rather than render
``unknown`` over paths that already hold the real value.
"""

from __future__ import annotations

import shutil
import zipfile
from collections.abc import Collection
from pathlib import Path

import structlog
from anafpy.efactura import DownloadedMessage
from pydantic import BaseModel, Field

from .config import ARTIFACT_SUFFIXES, SyncConfig
from .context import project_archived, read_view
from .state import Archive, CatalogEntry
from .template import PathTemplate, artifact_path

__all__ = ["ReprocessReport", "run_reprocess"]

logger = structlog.get_logger(__name__)

#: The template variables no archived artifact records, so no re-projection can
#: supply them. Just the one — everything else is either in the document or in
#: the catalog row.
_UNRECOVERABLE = frozenset({"request_id"})

#: The columns a re-projection is allowed to overwrite. The rest of the row
#: describes the download, which this pass did not repeat.
_REPROJECTED = (
    "issue_date",
    "number",
    "partner_name",
    "partner_cif",
    "total",
    "currency",
)


class ReprocessReport(BaseModel):
    """Outcome of one reprocess pass, for the CLI summary and the exit code."""

    scanned: int = 0
    #: Rows whose catalog columns actually changed.
    refreshed: int = 0
    would_refresh: int = 0  # dry-run only
    #: Messages whose files were relocated to a re-rendered path.
    moved: int = 0
    would_move: int = 0  # dry-run only
    #: Parseable UBL that anafpy's reader still refuses — the rule drift this
    #: pass exists to outlive. Counted apart from :attr:`skipped` because it is
    #: the only outcome that is a bug report: the fix is a newer anafpy, and
    #: then this same command finishes the job.
    unreadable: int = 0
    #: Nothing to re-read (neither ZIP nor XML on disk), or content that was
    #: never a UBL invoice. Either way the row is left exactly as it was.
    skipped: int = 0
    #: The re-rendered path is occupied by files this message does not own.
    #: Nothing is moved — the catalog is still refreshed, in place.
    conflicts: int = 0
    failures: list[tuple[str, str]] = Field(default_factory=list)  # (id, error)

    @property
    def ok(self) -> bool:
        return not self.failures


def run_reprocess(
    config: SyncConfig,
    state: Archive,
    *,
    message_ids: Collection[str] | None = None,
    move: bool = False,
    dry_run: bool = False,
) -> ReprocessReport:
    """Re-project every downloaded message from its own files.

    Reads no network at all: everything comes off disk, so an archive years past
    ANAF's window reprocesses exactly as well as today's.

    Args:
        config: the sync config — its output root and path template are what a
            move re-renders against.
        state: the archive to walk and update.
        message_ids: only these messages, instead of the whole archive — what
            the tray's per-invoice button asks for. Every id must name a
            downloaded row.
        move: also relocate each message's artifacts to the re-rendered path.
        dry_run: report what would change; write and move nothing.

    Raises:
        ValueError: ``move`` is asked for but the path template references a
            variable no archived artifact records (see :data:`_UNRECOVERABLE`);
            or ``message_ids`` names something this pass cannot act on.
    """
    template = PathTemplate(config.output.template)
    if move and (lost := sorted(template.variables & _UNRECOVERABLE)):
        named = ", ".join(f"{{{name}}}" for name in lost)
        raise ValueError(
            f"the path template references {named}, which nothing in the archive "
            f"records — paths cannot be re-rendered for it; reprocess without "
            f"--move to refresh the catalog alone"
        )
    report = ReprocessReport()
    root = config.output.resolved_directory
    worklist = state.synced(message_ids=message_ids)
    if message_ids is not None and (
        unknown := sorted(set(message_ids) - {entry.message_id for entry in worklist})
    ):
        # Refused whole rather than half-run: an id that names nothing (or names
        # a backfill row, which this pass excludes by design) is a mistake worth
        # seeing, not a silent zero.
        raise ValueError(
            f"not a downloaded message in this archive: {', '.join(unknown)}"
        )
    for entry in worklist:
        report.scanned += 1
        try:
            _reprocess_one(
                entry, state, template, report, root=root, move=move, dry_run=dry_run
            )
        except OSError as exc:
            # A file that cannot be read or moved is reported, never fatal: one
            # locked or vanished artifact must not abandon the rest of the walk.
            logger.error(
                "reprocess_failed", message_id=entry.message_id, error=str(exc)
            )
            report.failures.append((entry.message_id, str(exc)))
    logger.info(
        "reprocess_done",
        scanned=report.scanned,
        refreshed=report.refreshed or report.would_refresh,
        moved=report.moved or report.would_move,
        unreadable=report.unreadable,
    )
    return report


def _reprocess_one(
    entry: CatalogEntry,
    state: Archive,
    template: PathTemplate,
    report: ReprocessReport,
    *,
    root: Path,
    move: bool,
    dry_run: bool,
) -> None:
    """Re-project one row, or count why it was passed over."""
    log = logger.bind(message_id=entry.message_id)
    base = Path(entry.base_path)
    source = _stored_artifact(base)
    if source is None:
        log.warning("reprocess_no_source", path=str(base))
        report.skipped += 1
        return
    try:
        message = DownloadedMessage.from_zip(source.read_bytes())
    except zipfile.BadZipFile:
        # Not a ZIP: the XML fallback, which `view` reads through untouched.
        message = _message_from_xml(source.read_bytes())
    view = read_view(message)
    if view is None:
        if (error := message.view_error) is not None:
            # The pathology itself, unchanged: say so per message, so the log
            # names the documents worth reporting upstream.
            log.warning("view_unreadable", path=str(source), error=str(error))
            report.unreadable += 1
        else:
            report.skipped += 1
        return

    projection = project_archived(
        view,
        cif=entry.cif,
        direction=entry.direction,
        message_id=entry.message_id,
        message_type=entry.message_type,
        created=entry.created_at,
    )
    # The merge: re-derived columns win, everything only the listing knew stays.
    updated = entry.model_copy(
        update={key: projection.catalog[key] for key in _REPROJECTED}
    )
    refreshed = any(
        getattr(updated, key) != getattr(entry, key) for key in _REPROJECTED
    )

    target = base
    if move:
        rendered = root / Path(template.render(projection.context))
        target = state.claim_base(rendered, entry.message_id)
    if target != base:
        moves = _plan_move(base, target, read_from=source)
        if moves is None:
            # Refused, not failed: the catalog refresh below still lands, and
            # the row goes on pointing at files that are exactly where it says.
            log.warning("reprocess_conflict", old=str(base), new=str(target))
            report.conflicts += 1
            target = base
        elif dry_run:
            report.would_move += 1
            log.info("would_move", old=str(base), new=str(target))
        else:
            # Files first, row after: a crash between them leaves the row
            # pointing at a path with nothing under it — visible (the next pass
            # counts it `skipped`) and losing not one byte, whereas recording
            # the new path first would point the row at files that never got
            # there. Every earlier instant is fully resumable; see `_plan_move`.
            _apply_move(moves)
            _prune_empty(base.parent, root)
            report.moved += 1
            log.info("moved", old=str(base), new=str(target))

    if refreshed:
        log.info(
            "would_refresh" if dry_run else "refreshed",
            number=updated.number,
            partner=updated.partner_name,
        )
        if dry_run:
            report.would_refresh += 1
        else:
            report.refreshed += 1
    if not dry_run and (refreshed or target != base):
        state.reproject(updated.model_copy(update={"base_path": target.as_posix()}))


def _stored_artifact(base: Path) -> Path | None:
    """The file to re-read this message from: its ZIP, or the XML beside it.

    The ZIP is preferred — it is the archive's authoritative artifact, the one
    the original download parsed. An ``xml``-only archive still reprocesses:
    the content member is all the projection ever reads.
    """
    for suffix in (".zip", ".xml"):
        if (path := artifact_path(base, suffix)).exists():
            return path
    return None


def _message_from_xml(content: bytes) -> DownloadedMessage:
    """A message standing in for a stored XML with no ZIP beside it.

    ``raw_zip`` is required by the model and holds the same bytes here; nothing
    in this pass touches it, and nothing here is ever written back to disk.
    """
    return DownloadedMessage(raw_zip=content, content_xml=content)


def _plan_move(
    old: Path, new: Path, *, read_from: Path
) -> list[tuple[Path, Path]] | None:
    """Source/destination pairs for one message's files, or ``None`` on conflict.

    Every artifact suffix is considered, not just the ones the row lists: a
    sidecar written under an older config is still this message's file, and
    leaving it behind would orphan it.

    A destination that exists while its source is gone is simply a move this
    pass already made and did not get to record — planned around, so an
    interrupted run resumes. A destination that exists *while its source does
    too* is two documents wanting one name, and the safe answer is to move
    neither: ``None``.

    ``read_from`` is ordered last, so a crash part-way through the moves always
    leaves the file the next pass re-reads this message from where the catalog
    still says it is — the half-moved siblings are then planned around, and the
    pass simply finishes the job.
    """
    moves: list[tuple[Path, Path]] = []
    for suffix in ARTIFACT_SUFFIXES.values():
        src, dst = artifact_path(old, suffix), artifact_path(new, suffix)
        if not src.exists():
            continue
        if dst.exists():
            return None
        moves.append((src, dst))
    moves.sort(key=lambda pair: pair[0] == read_from)
    return moves


def _apply_move(moves: list[tuple[Path, Path]]) -> None:
    """Relocate one message's files, creating the destination folders."""
    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # `shutil.move`, not `Path.rename`: an archive root spanning a mount
        # point (a NAS folder, an external drive) would fail the rename outright.
        shutil.move(str(src), str(dst))


def _prune_empty(start: Path, root: Path) -> None:
    """Delete the folders a move emptied, climbing until one is not.

    Bounded strictly below ``root``, and only ever removes *empty* directories —
    the point is that the ``unknown/`` tree a bad projection created disappears
    with the last file that was in it, rather than lingering as a false lead.
    """
    current = start
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return  # not empty, or already gone — either way, stop climbing
        current = current.parent
