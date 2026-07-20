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
from anafpy.exceptions import AnafError

from anaf_sync.config import Artifact, OutputConfig, SyncConfig
from anaf_sync.engine import SyncReport, _sync_cif
from anaf_sync.state import SyncState
from anaf_sync.template import PathTemplate


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("4001.xml", "<NotUbl>plain</NotUbl>")
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
