"""Reprocess: re-derive a row's catalog fields, and its path, from its own files.

Fixtures are genuine CIUS-RO-valid UBL rendered by anafpy's authoring package,
like the backfill suite: the whole feature rests on `DownloadedMessage.view`
parsing what is actually on disk, so a fake that skips the parse would test
nothing. The pathology being repaired is simulated the way it presents — a row
whose XML-derived columns are all `None` and whose path says `unknown` — since
the drift that caused it is, by definition, whatever anafpy cannot yet read.
"""

from __future__ import annotations

import datetime as dt
import io
import sqlite3
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from anafpy.efactura.authoring import (
    InvoiceDocument,
    InvoiceLine,
    Party,
    PostalAddress,
    Seller,
)
from anafpy.efactura.authoring.build import render_invoice

from anaf_sync.config import Artifact, OutputConfig, SyncConfig
from anaf_sync.reprocess import run_reprocess
from anaf_sync.state import Archive, CatalogEntry

_ADDRESS = PostalAddress(
    street="Str. Test 1",
    city="SECTOR1",
    country="RO",
    postal_zone="010101",
    county="RO-B",
)

_TEMPLATE = "{cif}/{direction}/{issue_date:%Y}/{issue_date:%Y-%m-%d}_{number}"


def _invoice_xml(
    *,
    number: str = "1882",
    seller_name: str = "Miele Appliances S.R.L.",
    issue_date: dt.date = dt.date(2026, 6, 17),
) -> bytes:
    document = InvoiceDocument(
        number=number,
        issue_date=issue_date,
        due_date=issue_date + dt.timedelta(days=30),
        currency="RON",
        seller=Seller(name=seller_name, vat_id="RO222", address=_ADDRESS),
        buyer=Party(name="Client SRL", vat_id="RO111", address=_ADDRESS),
        lines=[
            InvoiceLine(
                name="Consultanta",
                quantity=Decimal(1),
                unit="H87",
                unit_price=Decimal("728.51"),
                vat_category="S",
                vat_rate=Decimal(19),
            )
        ],
    )
    return render_invoice(document)


def _write_zip(base: Path, xml: bytes) -> Path:
    """An ANAF-shaped download ZIP at ``base`` + ``.zip``."""
    base.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("6389056244.xml", xml)
        zf.writestr("semnatura_6389056244.xml", "<Signature/>")
    path = base.with_name(base.name + ".zip")
    path.write_bytes(buffer.getvalue())
    return path


def _config(tmp_path: Path, *, template: str = _TEMPLATE) -> SyncConfig:
    return SyncConfig(
        cifs=["111"],
        output=OutputConfig(
            directory=tmp_path / "archive",
            template=template,
            artifacts=[Artifact.ZIP, Artifact.PDF],
        ),
    )


def _blank_entry(base: Path, **overrides: object) -> CatalogEntry:
    """A row as an unreadable download left it: every XML-derived column None.

    The listing-derived ones are populated, because the listing *was* readable —
    that asymmetry is exactly what reprocess must preserve.
    """
    fields: dict[str, object] = {
        "message_id": "4001",
        "cif": "111",
        "direction": "received",
        "base_path": base.as_posix(),
        "artifacts": ["zip"],
        "message_type": "FACTURA PRIMITA",
        "created_at": dt.datetime(2026, 6, 18, 9, 30),
    }
    return CatalogEntry(**{**fields, **overrides})  # type: ignore[arg-type]


def _seed(tmp_path: Path, *, stem: str = "unknown/unknown_unknown") -> Archive:
    """An archive holding one blank row whose ZIP is on disk under ``stem``."""
    base = tmp_path / "archive" / stem
    _write_zip(base, _invoice_xml())
    state = Archive.open(tmp_path / "state.db")
    state.record(_blank_entry(base))
    return state


def test_refreshes_the_catalog_from_the_stored_zip(tmp_path: Path) -> None:
    state = _seed(tmp_path)

    report = run_reprocess(_config(tmp_path), state)

    assert (report.scanned, report.refreshed) == (1, 1)
    assert report.ok
    entry = state.catalog()[0]
    assert entry.number == "1882"
    assert entry.issue_date == dt.date(2026, 6, 17)
    assert entry.partner_name == "Miele Appliances S.R.L."
    assert entry.partner_cif == "222"
    assert entry.currency == "RON"
    assert entry.total == pytest.approx(866.93)


def _saved_at(path: Path, message_id: str) -> str:
    """Read ``saved_at`` straight from the DB — no reader exposes it."""
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT saved_at FROM messages WHERE message_id = ?", (message_id,)
    ).fetchone()
    conn.close()
    return str(row[0])


def test_keeps_what_only_the_listing_ever_knew(tmp_path: Path) -> None:
    state = _seed(tmp_path)
    before = _saved_at(tmp_path / "state.db", "4001")

    run_reprocess(_config(tmp_path), state, move=True)

    entry = state.catalog()[0]
    # Nothing here is derivable from the document, and `created_at` in
    # particular drives the delay flag — wiping it would invent "on time".
    assert entry.message_type == "FACTURA PRIMITA"
    assert entry.created_at == dt.datetime(2026, 6, 18, 9, 30)
    assert (entry.message_id, entry.cif, entry.direction) == ("4001", "111", "received")
    assert entry.source == "sync"
    # Nothing was downloaded, so the row goes on naming the run that archived it.
    assert _saved_at(tmp_path / "state.db", "4001") == before


def test_does_not_move_without_being_asked(tmp_path: Path) -> None:
    state = _seed(tmp_path)

    report = run_reprocess(_config(tmp_path), state)

    assert report.moved == 0
    assert (tmp_path / "archive/unknown/unknown_unknown.zip").exists()
    assert state.catalog()[0].base_path.endswith("unknown/unknown_unknown")


def test_move_relocates_the_files_and_the_row(tmp_path: Path) -> None:
    state = _seed(tmp_path)
    (tmp_path / "archive/unknown/unknown_unknown.pdf").write_bytes(b"%PDF-1.4")

    report = run_reprocess(_config(tmp_path), state, move=True)

    assert (report.moved, report.refreshed) == (1, 1)
    moved = tmp_path / "archive/111/received/2026/2026-06-17_1882"
    assert moved.with_name(moved.name + ".zip").exists()
    # Every artifact on disk travels, not just the ones the row lists.
    assert moved.with_name(moved.name + ".pdf").read_bytes() == b"%PDF-1.4"
    assert state.catalog()[0].base_path == moved.as_posix()
    # The folder the bad projection created goes with the last file in it.
    assert not (tmp_path / "archive/unknown").exists()


def test_move_leaves_the_output_root_standing(tmp_path: Path) -> None:
    """Pruning climbs to the root and stops — an archive is not a temp dir."""
    state = _seed(tmp_path, stem="unknown")

    run_reprocess(_config(tmp_path), state, move=True)

    assert (tmp_path / "archive").is_dir()


def test_dry_run_move_writes_nothing(tmp_path: Path) -> None:
    state = _seed(tmp_path)

    report = run_reprocess(_config(tmp_path), state, move=True, dry_run=True)

    assert (report.would_move, report.would_refresh) == (1, 1)
    assert (report.moved, report.refreshed) == (0, 0)
    assert (tmp_path / "archive/unknown/unknown_unknown.zip").exists()
    assert state.catalog()[0].number is None


def test_an_occupied_destination_refuses_the_move_but_still_refreshes(
    tmp_path: Path,
) -> None:
    state = _seed(tmp_path)
    target = tmp_path / "archive/111/received/2026/2026-06-17_1882.zip"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone else's file")

    report = run_reprocess(_config(tmp_path), state, move=True)

    assert (report.conflicts, report.moved, report.refreshed) == (1, 0, 1)
    assert target.read_bytes() == b"someone else's file"
    entry = state.catalog()[0]
    assert entry.number == "1882"  # the catalog is fixed either way
    assert entry.base_path.endswith("unknown/unknown_unknown")  # still true of disk


def test_resumes_a_half_finished_move(tmp_path: Path) -> None:
    """A destination whose source is gone was already moved, not a conflict."""
    state = _seed(tmp_path)
    old = tmp_path / "archive/unknown/unknown_unknown"
    moved_pdf = tmp_path / "archive/111/received/2026/2026-06-17_1882.pdf"
    moved_pdf.parent.mkdir(parents=True)
    moved_pdf.write_bytes(b"%PDF-1.4")  # a prior run's move, interrupted

    report = run_reprocess(_config(tmp_path), state, move=True)

    assert (report.moved, report.conflicts) == (1, 0)
    assert moved_pdf.with_name("2026-06-17_1882.zip").exists()
    assert not old.with_name("unknown_unknown.zip").exists()


def test_reprocesses_an_xml_only_archive(tmp_path: Path) -> None:
    base = tmp_path / "archive" / "unknown" / "unknown_unknown"
    base.parent.mkdir(parents=True)
    base.with_name(base.name + ".xml").write_bytes(_invoice_xml())
    state = Archive.open(tmp_path / "state.db")
    state.record(_blank_entry(base, artifacts=["xml"]))

    report = run_reprocess(_config(tmp_path), state, move=True)

    assert (report.refreshed, report.moved) == (1, 1)
    assert (tmp_path / "archive/111/received/2026/2026-06-17_1882.xml").exists()


def test_skips_a_row_with_nothing_left_on_disk(tmp_path: Path) -> None:
    state = Archive.open(tmp_path / "state.db")
    state.record(_blank_entry(tmp_path / "archive" / "gone" / "invoice"))

    report = run_reprocess(_config(tmp_path), state)

    assert (report.skipped, report.refreshed) == (1, 0)
    assert report.ok  # an operator deleting their own files is not our failure


def test_skips_content_that_was_never_an_invoice(tmp_path: Path) -> None:
    base = tmp_path / "archive" / "erori" / "4001"
    _write_zip(base, b"<NotUbl>plain</NotUbl>")
    state = Archive.open(tmp_path / "state.db")
    state.record(_blank_entry(base))

    report = run_reprocess(_config(tmp_path), state)

    assert (report.skipped, report.unreadable) == (1, 0)


def test_leaves_backfilled_rows_alone(tmp_path: Path) -> None:
    """They catalog folders the engine does not own — moving them is not ours."""
    base = tmp_path / "vechi" / "factura-1"
    _write_zip(base, _invoice_xml())
    state = Archive.open(tmp_path / "state.db")
    state.record(_blank_entry(base, message_id="backfill:abc", source="backfill"))

    report = run_reprocess(_config(tmp_path), state, move=True)

    assert report.scanned == 0
    assert base.with_name("factura-1.zip").exists()


def test_refuses_to_move_under_a_template_it_cannot_render(tmp_path: Path) -> None:
    state = _seed(tmp_path)

    with pytest.raises(ValueError, match="request_id"):
        run_reprocess(
            _config(tmp_path, template="{request_id}/{number}"), state, move=True
        )


def test_that_template_still_refreshes_the_catalog(tmp_path: Path) -> None:
    """The refusal is about paths only — the columns have no such dependency."""
    state = _seed(tmp_path)

    report = run_reprocess(_config(tmp_path, template="{request_id}/{number}"), state)

    assert report.refreshed == 1


def test_a_second_pass_has_nothing_to_do(tmp_path: Path) -> None:
    state = _seed(tmp_path)
    run_reprocess(_config(tmp_path), state, move=True)

    report = run_reprocess(_config(tmp_path), state, move=True)

    assert (report.scanned, report.refreshed, report.moved) == (1, 0, 0)


def test_targets_a_single_message(tmp_path: Path) -> None:
    """What the tray's per-invoice button runs — one row, not the archive."""
    state = _seed(tmp_path)
    other = tmp_path / "archive" / "unknown" / "unknown_altul"
    _write_zip(other, _invoice_xml(number="9999"))
    state.record(_blank_entry(other, message_id="4002"))

    report = run_reprocess(_config(tmp_path), state, message_ids=["4001"], move=True)

    assert (report.scanned, report.refreshed, report.moved) == (1, 1, 1)
    by_id = {entry.message_id: entry for entry in state.catalog()}
    assert by_id["4001"].number == "1882"
    assert by_id["4002"].number is None  # untouched
    assert other.with_name("unknown_altul.zip").exists()


def test_an_unknown_id_is_refused_rather_than_silently_empty(tmp_path: Path) -> None:
    state = _seed(tmp_path)

    with pytest.raises(ValueError, match="nope"):
        run_reprocess(_config(tmp_path), state, message_ids=["4001", "nope"])

    assert state.catalog()[0].number is None  # refused whole, not half-run


def test_a_backfilled_id_is_refused_too(tmp_path: Path) -> None:
    """`synced` excludes them by design, so naming one is a mistake, not a no-op."""
    base = tmp_path / "vechi" / "factura-1"
    _write_zip(base, _invoice_xml())
    state = Archive.open(tmp_path / "state.db")
    state.record(_blank_entry(base, message_id="backfill:abc", source="backfill"))

    with pytest.raises(ValueError, match="backfill:abc"):
        run_reprocess(_config(tmp_path), state, message_ids=["backfill:abc"])
