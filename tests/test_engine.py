"""End-to-end engine pass against a fake e-Factura client."""

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from anafpy.efactura import (
    DownloadedMessage,
    EFacturaClient,
    Filter,
    MessageListItem,
)
from anafpy.efactura.authoring import DocumentKind, InvoiceDocument
from anafpy.exceptions import AnafError
from anafpy.public import TransformStandard

from anaf_sync.config import Artifact, OutputConfig, SyncConfig
from anaf_sync.engine import SyncReport, _sync_cif, _transform_standard
from anaf_sync.state import SyncState
from anaf_sync.template import PathTemplate


def _zip_bytes(*, with_signature: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("4001.xml", "<NotUbl>plain</NotUbl>")
        if with_signature:
            zf.writestr("semnatura_4001.xml", "<Signature/>")
    return buffer.getvalue()


class FakeClient:
    """Just enough of EFacturaClient for the engine."""

    def __init__(self, items: list[MessageListItem]) -> None:
        self.items = items
        self.downloads: list[str] = []

    def list_messages(
        self,
        *,
        cif: str,
        days: int | None = None,
        filter: Filter | None = None,  # noqa: A002
    ) -> AsyncIterator[MessageListItem]:
        async def gen() -> AsyncIterator[MessageListItem]:
            for item in self.items:
                yield item

        return gen()

    async def download(self, message_id: str) -> DownloadedMessage:
        self.downloads.append(message_id)
        return DownloadedMessage.from_zip(_zip_bytes())


class FailingClient(FakeClient):
    """Every download raises, as a persistently broken message would."""

    async def download(self, message_id: str) -> DownloadedMessage:
        self.downloads.append(message_id)
        raise AnafError("boom")


def _config(tmp_path: Path) -> SyncConfig:
    return SyncConfig(
        cifs=["111"],
        output=OutputConfig(
            directory=tmp_path / "archive",
            template="{cif}/{direction}/{message_id}",
            artifacts=[
                Artifact.ZIP,
                Artifact.XML,
                Artifact.SIGNATURE,
                Artifact.METADATA,
            ],
        ),
    )


def _items() -> list[MessageListItem]:
    invoice = MessageListItem(
        id="m1",
        request_id="r1",
        message_type="FACTURA PRIMITA",
        created_at="202607181430",
        cif="111",
        details="Factura cu id_incarcare=r1 emisa de cif_emitent=222 "
        "pentru cif_beneficiar=111",
    )
    error_notice = MessageListItem(
        id="m2", message_type="ERORI FACTURA", cif="111", details="Erori"
    )
    return [invoice, error_notice]


async def _run(client: FakeClient, config: SyncConfig, state: SyncState) -> SyncReport:
    report = SyncReport()
    await _sync_cif(
        cast(EFacturaClient, client),
        None,
        config,
        state,
        PathTemplate(config.output.template),
        report,
        cif="111",
        days=60,
        dry_run=False,
        redownload=False,
    )
    return report


async def test_downloads_new_invoices_and_writes_artifacts(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")
    client = FakeClient(_items())

    report = await _run(client, config, state)

    assert report.downloaded == 1
    assert report.skipped_non_invoice == 1
    assert report.ok
    base = tmp_path / "archive" / "111" / "received" / "m1"
    assert base.with_suffix(".zip").exists()
    assert base.with_suffix(".xml").exists()
    assert Path(f"{base}_semnatura.xml").exists()
    assert base.with_suffix(".json").exists()
    assert state.is_downloaded("m1")


async def test_second_run_is_idempotent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")
    client = FakeClient(_items())

    await _run(client, config, state)
    report = await _run(client, config, state)

    assert report.downloaded == 0
    assert report.already_archived == 1
    assert client.downloads == ["m1"]  # downloaded exactly once across both runs


async def test_failures_are_recorded_and_cleared_on_success(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")

    report = await _run(FailingClient(_items()), config, state)
    assert report.failures == [("m1", "boom")]
    assert state.failures["m1"].attempts == 1
    # persisted, not just in memory: a crash must not lose the trace
    assert "m1" in SyncState.load(tmp_path / "state.json").failures

    await _run(FailingClient(_items()), config, state)
    assert state.failures["m1"].attempts == 2

    await _run(FakeClient(_items()), config, state)  # retried despite the record
    assert state.is_downloaded("m1")
    assert "m1" not in state.failures


def _invoice(message_id: str) -> MessageListItem:
    return MessageListItem(
        id=message_id,
        message_type="FACTURA PRIMITA",
        created_at="202607181430",
        cif="111",
        details="Factura cu id_incarcare=r1 emisa de cif_emitent=222 "
        "pentru cif_beneficiar=111",
    )


async def test_colliding_paths_get_an_id_suffix(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.output.template = "{cif}/factura"  # every message renders the same base
    state = SyncState.load(tmp_path / "state.json")
    client = FakeClient([_invoice("m1"), _invoice("m3")])

    report = await _run(client, config, state)

    assert report.downloaded == 2
    folder = tmp_path / "archive" / "111"
    assert (folder / "factura.zip").exists()
    assert (folder / "factura_m3.zip").exists()
    assert state.owner_of(str(folder / "factura")) == "m1"


async def test_redownload_overwrites_in_place(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")
    client = FakeClient(_items())

    await _run(client, config, state)
    report = SyncReport()
    await _sync_cif(
        cast(EFacturaClient, client),
        None,
        config,
        state,
        PathTemplate(config.output.template),
        report,
        cif="111",
        days=60,
        dry_run=False,
        redownload=True,
    )

    assert report.downloaded == 1
    folder = tmp_path / "archive" / "111" / "received"
    # The refresh reuses the original base — no `m1_m1` duplicates.
    assert sorted(p.name for p in folder.iterdir()) == [
        "m1.json",
        "m1.xml",
        "m1.zip",
        "m1_semnatura.xml",
    ]


async def test_crash_leftovers_are_overwritten_not_duplicated(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")
    client = FakeClient(_items())
    # A previous run died after writing the zip but before recording state.
    base = tmp_path / "archive" / "111" / "received" / "m1"
    base.parent.mkdir(parents=True)
    base.with_suffix(".zip").write_bytes(b"truncated")

    report = await _run(client, config, state)

    assert report.downloaded == 1
    assert base.with_suffix(".zip").read_bytes() == _zip_bytes()  # healed
    assert not (base.parent / "m1_m1.zip").exists()


async def test_state_records_only_artifacts_actually_written(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")

    class NoSignatureClient(FakeClient):
        async def download(self, message_id: str) -> DownloadedMessage:
            return DownloadedMessage.from_zip(_zip_bytes(with_signature=False))

    await _run(NoSignatureClient(_items()), config, state)

    record = state.record_of("m1")
    assert record is not None
    assert record.artifacts == ["zip", "xml", "metadata"]  # no signature member


async def test_messages_without_id_are_counted_not_failed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")
    ghost = MessageListItem(message_type="FACTURA PRIMITA", cif="111")
    client = FakeClient([ghost, *_items()])

    report = await _run(client, config, state)

    assert report.missing_id == 1
    assert report.downloaded == 1
    assert report.ok  # nothing actionable — the run must not exit non-zero


def test_transform_standard_follows_document_kind() -> None:
    invoice = InvoiceDocument.model_construct(kind=DocumentKind.INVOICE)
    credit_note = InvoiceDocument.model_construct(kind=DocumentKind.CREDIT_NOTE)
    assert _transform_standard(invoice) is TransformStandard.INVOICE
    assert _transform_standard(credit_note) is TransformStandard.CREDIT_NOTE
    assert _transform_standard(None) is TransformStandard.INVOICE


async def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    state = SyncState.load(tmp_path / "state.json")
    client = FakeClient(_items())

    report = SyncReport()
    await _sync_cif(
        cast(EFacturaClient, client),
        None,
        config,
        state,
        PathTemplate(config.output.template),
        report,
        cif="111",
        days=60,
        dry_run=True,
        redownload=False,
    )

    assert report.would_download == 1
    assert client.downloads == []
    assert not (tmp_path / "archive").exists()
    assert not state.is_downloaded("m1")
