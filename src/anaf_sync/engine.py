"""The sync engine: list, download, render the path, write artifacts.

Each run lists every message in the window and downloads the ones the state
file has not seen. Downloads are GETs, so transient network failures and rate
limits are retried with backoff; a message that still fails is reported and
retried naturally on the next scheduled run.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import structlog
from anafpy.auth import TokenProvider
from anafpy.efactura import (
    DownloadedMessage,
    EFacturaClient,
    Filter,
    MessageListItem,
)
from anafpy.efactura.authoring import DocumentKind, InvoiceDocument
from anafpy.exceptions import AnafError, AnafRateLimitError, AnafTransportError
from anafpy.public import PublicClient, TransformStandard
from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import Artifact, Direction, SyncConfig
from .context import direction_of, project_message
from .state import Archive, CatalogEntry
from .template import PathTemplate

__all__ = ["SyncReport", "run_sync"]

logger = structlog.get_logger(__name__)

_FILTERS: dict[Direction, Filter | None] = {
    Direction.RECEIVED: Filter.RECEIVED,
    Direction.SENT: Filter.SENT,
    Direction.BOTH: None,
}


class SyncReport(BaseModel):
    """Outcome of one sync run, for the CLI summary and the exit code."""

    listed: int = 0
    already_archived: int = 0
    skipped_non_invoice: int = 0
    missing_id: int = 0  # listed without an id — nothing actionable, not a failure
    downloaded: int = 0
    would_download: int = 0  # dry-run only
    failures: list[tuple[str, str]] = Field(default_factory=list)  # (id, error)

    @property
    def ok(self) -> bool:
        return not self.failures


async def run_sync(
    config: SyncConfig,
    provider: TokenProvider,
    state: Archive,
    *,
    days: int | None = None,
    dry_run: bool = False,
    redownload: bool = False,
) -> SyncReport:
    """Run one full sync pass over every configured CIF."""
    report = SyncReport()
    template = PathTemplate(config.output.template)
    # Always production: TEST's inbox only ever holds self-uploaded fixtures,
    # and every operation here is a read — --dry-run is the safety valve.
    async with EFacturaClient(provider) as client:
        public = PublicClient() if Artifact.PDF in config.output.artifacts else None
        try:
            for cif in config.cifs:
                await _sync_cif(
                    client,
                    public,
                    config,
                    state,
                    template,
                    report,
                    cif=cif,
                    days=days or config.lookback_days,
                    dry_run=dry_run,
                    redownload=redownload,
                )
        finally:
            if public is not None:
                await public.aclose()
    return report


async def _sync_cif(
    client: EFacturaClient,
    public: PublicClient | None,
    config: SyncConfig,
    state: Archive,
    template: PathTemplate,
    report: SyncReport,
    *,
    cif: str,
    days: int,
    dry_run: bool,
    redownload: bool,
) -> None:
    log = logger.bind(cif=cif)
    log.info("listing_messages", days=days, direction=config.direction.value)

    # Materialise the listing first so a paging error surfaces before any
    # downloads, and the count is known up front.
    items = [
        item
        async for item in client.list_messages(
            cif=cif, days=days, filter=_FILTERS[config.direction]
        )
    ]
    report.listed += len(items)
    log.info("listing_done", messages=len(items))

    for item in items:
        message_id = item.id
        if message_id is None:
            # Nothing to download and nothing the user can do about it.
            log.warning("message_without_id", type=item.message_type)
            report.missing_id += 1
            continue
        if direction_of(item) is None:
            # Error notices and buyer messages carry no invoice to archive.
            report.skipped_non_invoice += 1
            continue
        if state.is_archived(message_id) and not redownload:
            report.already_archived += 1
            continue
        if dry_run:
            report.would_download += 1
            log.info(
                "would_download",
                message_id=message_id,
                type=item.message_type,
                details=item.details,
            )
            continue
        try:
            entry = await _archive_message(
                client, public, config, state, template, item, cif=cif
            )
        except AnafError as exc:
            log.error("download_failed", message_id=message_id, error=str(exc))
            report.failures.append((message_id, str(exc)))
            state.record_failure(message_id, str(exc))  # visibility only, no gate
            continue
        # record() commits one transaction: a crash never redoes or loses work.
        state.record(entry)
        report.downloaded += 1
        log.info(
            "archived",
            message_id=message_id,
            path=entry.base_path,
            artifacts=entry.artifacts,
        )


async def _download_with_retry(
    client: EFacturaClient, message_id: str
) -> DownloadedMessage:
    # descarcare is an idempotent GET: retrying is safe. Rate limits get the
    # same treatment with the backoff growing into tens of seconds.
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type((AnafTransportError, AnafRateLimitError)),
        wait=wait_exponential_jitter(initial=2.0, max=60.0),
        stop=stop_after_attempt(4),
        reraise=True,
    ):
        with attempt:
            return await client.download(message_id)
    raise AssertionError("unreachable")  # pragma: no cover


async def _archive_message(
    client: EFacturaClient,
    public: PublicClient | None,
    config: SyncConfig,
    state: Archive,
    template: PathTemplate,
    item: MessageListItem,
    *,
    cif: str,
) -> CatalogEntry:
    """Download one message, write its artifacts, and build its catalog entry.

    The entry describes what is on disk (the base path, the artifact values
    actually written), not what was configured, plus the best-effort catalog
    fields projected from the message and its view.
    """
    assert item.id is not None
    direction = direction_of(item)
    assert direction is not None  # non-invoice messages are filtered upstream
    message = await _download_with_retry(client, item.id)
    projection = project_message(item, message.view, cif=cif)
    base = state.claim_base(
        config.output.resolved_directory / Path(template.render(projection.context)),
        item.id,
    )
    base.parent.mkdir(parents=True, exist_ok=True)

    written = []
    for artifact in config.output.artifacts:
        path = await _write_artifact(
            artifact, base, message, item, projection.context, public
        )
        if path is not None:
            written.append(artifact.value)
    return CatalogEntry(
        message_id=item.id,
        cif=cif,
        direction=direction,
        base_path=base.as_posix(),
        artifacts=written,
        **projection.catalog,
    )


async def _write_artifact(
    artifact: Artifact,
    base: Path,
    message: DownloadedMessage,
    item: MessageListItem,
    context: dict[str, object],
    public: PublicClient | None,
) -> Path | None:
    """Write one artifact next to ``base``; returns its path, or ``None`` when
    the message has nothing to satisfy it (e.g. no signature member)."""
    if artifact is Artifact.ZIP:
        path = base.with_suffix(".zip")
        path.write_bytes(message.raw_zip)
        return path
    if artifact is Artifact.XML:
        if message.content_xml is None:
            logger.warning("no_content_xml", message_id=item.id)
            return None
        path = base.with_suffix(".xml")
        path.write_bytes(message.content_xml)
        return path
    if artifact is Artifact.SIGNATURE:
        if message.signature_xml is None:
            logger.warning("no_signature_xml", message_id=item.id)
            return None
        path = Path(f"{base}_semnatura.xml")
        path.write_bytes(message.signature_xml)
        return path
    if artifact is Artifact.METADATA:
        path = base.with_suffix(".json")
        payload = {
            "message": item.model_dump(),
            "context": context,
            "archived_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        path.write_text(
            json.dumps(payload, default=str, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
    if artifact is Artifact.PDF:
        return await _write_pdf(base, message, item, public)
    raise AssertionError(f"unhandled artifact {artifact}")  # pragma: no cover


def _transform_standard(view: InvoiceDocument | None) -> TransformStandard:
    """Transformare rejects a credit note posted under the invoice standard."""
    if view is not None and view.kind is DocumentKind.CREDIT_NOTE:
        return TransformStandard.CREDIT_NOTE
    return TransformStandard.INVOICE


async def _write_pdf(
    base: Path,
    message: DownloadedMessage,
    item: MessageListItem,
    public: PublicClient | None,
) -> Path | None:
    """Render the invoice to PDF via ANAF's public transformare service."""
    if public is None or message.content_xml is None:
        return None
    # The document already passed validation at filing; skip re-validation.
    body = await public.render_invoice_pdf(
        message.content_xml,
        standard=_transform_standard(message.view),
        validate=False,
    )
    if not body.startswith(b"%PDF"):
        logger.warning("pdf_render_failed", message_id=item.id, body=body[:120])
        return None
    path = base.with_suffix(".pdf")
    path.write_bytes(body)
    return path
