"""Live sync tests against the real ANAF production endpoints (opt-in).

Unlike anafpy's roundtrip suites, nothing here ever files a document: anaf-sync
is a pure reader, so every test is strictly read-only — listing, downloading,
and the public ``transformare`` PDF rendering, exactly what a scheduled run
does. Archives and databases land in pytest tmp dirs; the user's real archive
and ``state.db`` are never touched.

Needs real credentials + a token store from ``anafpy auth login`` (a repo-root
``.env`` is loaded by conftest); run explicitly:

    ANAFPY_LIVE=1 uv run pytest -q -m live tests/test_sync_live.py

Auth is resolved through anaf-sync's own :class:`AuthSettings` seam — the same
path the CLI takes — so these tests also keep the shared-login convention
honest against a real ``anafpy auth login``.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from anafpy.auth import TokenProvider
from anafpy.efactura import EFacturaClient, MessageListItem

from anaf_sync.config import (
    Artifact,
    AuthSettings,
    Direction,
    OutputConfig,
    SyncConfig,
)
from anaf_sync.context import direction_of
from anaf_sync.engine import run_sync
from anaf_sync.state import Archive, CatalogEntry

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ANAFPY_LIVE") != "1",
        reason="live ANAF tests are opt-in (set ANAFPY_LIVE=1)",
    ),
]


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set (see .env)")
    return value


@pytest.fixture
async def provider() -> AsyncIterator[TokenProvider]:
    """A refreshing provider over the developer's real login, built through
    anaf-sync's own ``AuthSettings`` — exactly like the CLI."""
    settings = AuthSettings.from_env()
    if not settings.client_id or not settings.client_secret:
        pytest.skip("ANAFPY_CLIENT_ID / ANAFPY_CLIENT_SECRET not set (see .env)")
    if settings.build_store().load() is None:
        pytest.skip(
            "no tokens in the configured store — run `anafpy auth login` first "
            "(file-based logins also need ANAFPY_TOKEN_STORE_BACKEND=file)"
        )
    prov = settings.build_provider()
    yield prov
    await prov.aclose()


@pytest.fixture
def cif() -> str:
    return _require("ANAFPY_CIF")


def _config(tmp_path: Path, cif: str, artifacts: list[Artifact]) -> SyncConfig:
    return SyncConfig(
        cifs=[cif],
        direction=Direction.BOTH,
        lookback_days=60,
        output=OutputConfig(directory=tmp_path / "archive", artifacts=artifacts),
    )


async def _list_window(provider: TokenProvider, cif: str) -> list[MessageListItem]:
    async with EFacturaClient(provider) as client:
        return [item async for item in client.list_messages(cif=cif, days=60)]


async def test_listing_shape(provider: TokenProvider, cif: str) -> None:
    """Every listed message carries the fields the engine and context rely on.

    The engine keys idempotence on ``id`` and direction-classifies on ``tip``
    (``message_type``); if ANAF drops or renames either, this is the tripwire.
    """
    items = await _list_window(provider, cif)
    for item in items:
        assert item.id
        assert item.request_id
        assert item.message_type
    if items:
        # `tip` wordings must still classify: an all-None result would mean
        # ANAF renamed FACTURA PRIMITA/TRIMISA and the engine archives nothing.
        assert {direction_of(item) for item in items} != {None}


async def test_dry_run_reports_without_writing(
    provider: TokenProvider, cif: str, tmp_path: Path
) -> None:
    """A dry run over the full window reports coherently and writes nothing."""
    config = _config(tmp_path, cif, [Artifact.ZIP])
    state = Archive.open(tmp_path / "state.db")  # no retention: read-only intent

    report = await run_sync(config, provider, state, dry_run=True)

    assert report.ok, report.failures
    assert report.downloaded == 0
    assert report.already_archived == 0  # fresh state
    assert report.would_download + report.skipped_non_invoice == report.listed
    assert state.count == 0  # dry runs must not record anything
    assert not state.failures
    assert not config.output.resolved_directory.exists()  # ...or the archive


async def test_archives_one_message_end_to_end(
    provider: TokenProvider, cif: str, tmp_path: Path
) -> None:
    """Archive exactly ONE real message through the full engine path, then
    prove idempotence on a second run.

    Downloading the whole 60-day window would be needlessly heavy against
    production, so every other listed id is pre-seeded into a throwaway state
    file — the engine then sees a single new message. All five artifact
    writers run; the PDF exercises the public ``transformare`` service.
    """
    items = await _list_window(provider, cif)
    target = next(
        (item for item in items if item.id and direction_of(item) is not None),
        None,
    )
    if target is None:
        pytest.skip("no archivable invoice in ANAF's 60-day window")
    assert target.id is not None

    state = Archive.open(tmp_path / "state.db")
    for item in items:
        if item.id and item.id != target.id:
            # Unique base per id: base_path is UNIQUE in the catalog now.
            state.record(
                CatalogEntry(
                    message_id=item.id,
                    cif=cif,
                    direction=direction_of(item) or "received",
                    base_path=f"preseeded/{item.id}",
                    artifacts=[],
                )
            )

    config = _config(tmp_path, cif, list(Artifact))
    report = await run_sync(config, provider, state)

    assert report.ok, report.failures
    # A message indexed between our listing and the engine's would bump this;
    # lista indexing lags filing by minutes, so the race is negligible.
    assert report.downloaded == 1
    assert state.is_archived(target.id)

    root = config.output.resolved_directory
    zips = list(root.rglob("*.zip"))
    assert len(zips) == 1
    assert zips[0].read_bytes().startswith(b"PK")  # the raw descarcare archive
    sidecars = list(root.rglob("*.json"))
    assert len(sidecars) == 1
    payload = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert payload["context"]["message_id"] == target.id
    # Path safety must hold against real-world party names, not just fixtures.
    for path in root.rglob("*"):
        assert path.resolve().is_relative_to(root.resolve())

    again = await run_sync(config, provider, state)
    assert again.ok, again.failures
    assert again.downloaded == 0
    assert again.already_archived >= 1
