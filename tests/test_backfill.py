"""Backfill: catalog invoices already on disk that ANAF no longer lists.

Fixtures are genuine CIUS-RO-valid UBL rendered by anafpy's authoring package,
not hand-written XML — the whole feature rests on `DownloadedMessage.view`
parsing what is actually on disk, so a fake that skips the parse would test
nothing.
"""

from __future__ import annotations

import datetime as dt
import io
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

from anaf_sync.backfill import run_backfill
from anaf_sync.config import Artifact, OutputConfig, SyncConfig
from anaf_sync.state import Archive, CatalogEntry

_ADDRESS = PostalAddress(
    street="Str. Test 1",
    city="SECTOR1",
    country="RO",
    postal_zone="010101",
    county="RO-B",
)


def _buyer(cif: str = "RO111", *, identified_by: str = "vat_id") -> Party:
    """A buyer carrying its CIF in one of the homes CIUS-RO allows.

    BR-RO-120 accepts the legal-entity CompanyID *or any* PartyTaxScheme
    CompanyID, and production invoices use all of them: a VAT-registered buyer
    fills ``vat_id``, one below the registration threshold fills
    ``tax_registration_id`` (rendered with the ``!VAT`` marker), and some carry
    only ``legal_registration_id``. Each shape appears in a real archive.
    """
    bare = cif.removeprefix("RO")
    field = {"vat_id": cif, "tax_registration_id": bare, "legal_registration_id": bare}
    return Party(
        name="Client SRL", address=_ADDRESS, **{identified_by: field[identified_by]}
    )


def _invoice_xml(
    *,
    number: str = "1882",
    seller_cif: str = "RO222",
    buyer_cif: str = "RO111",
    buyer_identified_by: str = "vat_id",
    seller_name: str = "Miele Appliances S.R.L.",
    issue_date: dt.date = dt.date(2026, 6, 17),
    unit_price: str = "728.51",
) -> bytes:
    document = InvoiceDocument(
        number=number,
        issue_date=issue_date,
        due_date=issue_date + dt.timedelta(days=30),
        currency="RON",
        seller=Seller(name=seller_name, vat_id=seller_cif, address=_ADDRESS),
        buyer=_buyer(buyer_cif, identified_by=buyer_identified_by),
        lines=[
            InvoiceLine(
                name="Consultanta",
                quantity=Decimal(1),
                unit="H87",
                unit_price=Decimal(unit_price),
                vat_category="S",
                vat_rate=Decimal(19),
            )
        ],
    )
    return render_invoice(document)


def _write_zip(base: Path, xml: bytes, *, stem: str = "6389056244") -> Path:
    """An ANAF-shaped download ZIP at ``base`` + ``.zip``."""
    base.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{stem}.xml", xml)
        zf.writestr(f"semnatura_{stem}.xml", "<Signature/>")
    path = base.with_name(base.name + ".zip")
    path.write_bytes(buffer.getvalue())
    return path


def _config(tmp_path: Path) -> SyncConfig:
    return SyncConfig(
        cifs=["111"],
        output=OutputConfig(
            directory=tmp_path / "archive", artifacts=[Artifact.ZIP, Artifact.PDF]
        ),
    )


def test_indexes_an_invoice_from_disk(tmp_path: Path) -> None:
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "factura-1", _invoice_xml())
    state = Archive.open(tmp_path / "state.db")

    report = run_backfill(legacy, _config(tmp_path), state)

    assert (report.scanned, report.indexed) == (1, 1)
    assert report.ok
    entry = state.catalog()[0]
    assert entry.number == "1882"
    assert entry.issue_date == dt.date(2026, 6, 17)
    assert entry.partner_name == "Miele Appliances S.R.L."
    assert entry.partner_cif == "222"
    assert entry.currency == "RON"
    assert entry.total == pytest.approx(866.93)
    assert entry.cif == "111"
    assert entry.direction == "received"  # our CIF is the buyer
    assert entry.source == "backfill"
    # Unrecoverable off disk: they exist only in ANAF's listing.
    assert entry.message_type is None
    assert entry.created_at is None
    assert entry.message_id.startswith("backfill:")


def test_direction_comes_from_the_document_not_a_listing(tmp_path: Path) -> None:
    """Our CIF as the seller means we sent it; `tip` is not available here."""
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "trimisa", _invoice_xml(seller_cif="RO111", buyer_cif="RO999"))
    state = Archive.open(tmp_path / "state.db")

    run_backfill(legacy, _config(tmp_path), state)

    entry = state.catalog()[0]
    assert entry.direction == "sent"
    assert entry.partner_cif == "999"  # the partner is the *other* party


@pytest.mark.parametrize(
    "identified_by", ["vat_id", "tax_registration_id", "legal_registration_id"]
)
def test_buyer_is_recognised_however_it_is_identified(
    tmp_path: Path, identified_by: str
) -> None:
    """All three homes BR-RO-120 allows, all three seen in one real archive.

    Matching on ``vat_id`` alone files a buyer below the VAT-registration
    threshold as someone else's invoice — 6 of 9 real ones, silently dropped.
    """
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "primita", _invoice_xml(buyer_identified_by=identified_by))
    state = Archive.open(tmp_path / "state.db")

    report = run_backfill(legacy, _config(tmp_path), state)

    assert (report.indexed, report.foreign) == (1, 0)
    entry = state.catalog()[0]
    assert entry.direction == "received"
    assert entry.cif == "111"


def test_invoices_between_other_parties_are_skipped(tmp_path: Path) -> None:
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "straina", _invoice_xml(seller_cif="RO888", buyer_cif="RO999"))
    state = Archive.open(tmp_path / "state.db")

    report = run_backfill(legacy, _config(tmp_path), state)

    assert (report.foreign, report.indexed) == (1, 0)
    assert state.count == 0


def test_non_ubl_zips_are_counted_not_failed(tmp_path: Path) -> None:
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "eroare", b"<NotUbl>erori de validare</NotUbl>")
    state = Archive.open(tmp_path / "state.db")

    report = run_backfill(legacy, _config(tmp_path), state)

    assert (report.not_ubl, report.indexed) == (1, 0)
    assert report.ok  # nothing actionable — the run must not exit non-zero


def test_rerun_updates_rather_than_duplicates(tmp_path: Path) -> None:
    """Identity is the document's own digest, so a second pass is idempotent."""
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "factura-1", _invoice_xml())
    state = Archive.open(tmp_path / "state.db")

    run_backfill(legacy, _config(tmp_path), state)
    report = run_backfill(legacy, _config(tmp_path), state)

    assert report.indexed == 1  # same row, rewritten
    assert state.count == 1


def test_synced_rows_are_never_overwritten(tmp_path: Path) -> None:
    """Rebuilding over the live archive must not clobber real ANAF ids.

    `base_path` is UNIQUE, so this is also what stops the insert raising.
    """
    archive_dir = tmp_path / "archive"
    _write_zip(archive_dir / "factura-1", _invoice_xml())
    state = Archive.open(tmp_path / "state.db")
    state.record(
        CatalogEntry(
            message_id="7537618130",
            cif="111",
            direction="received",
            base_path=(archive_dir / "factura-1").as_posix(),
            artifacts=["zip"],
        )
    )

    report = run_backfill(archive_dir, _config(tmp_path), state)

    assert (report.already_known, report.indexed) == (1, 0)
    assert state.catalog()[0].message_id == "7537618130"
    assert state.catalog()[0].source == "sync"


def test_records_only_artifacts_present_on_disk(tmp_path: Path) -> None:
    legacy = tmp_path / "vechi"
    base = legacy / "factura-1"
    _write_zip(base, _invoice_xml())
    base.with_name(base.name + ".pdf").write_bytes(b"%PDF-1.4 fake")
    state = Archive.open(tmp_path / "state.db")

    run_backfill(legacy, _config(tmp_path), state)

    assert state.catalog()[0].artifacts == ["zip", "pdf"]


def test_dotted_base_resolves_its_siblings(tmp_path: Path) -> None:
    """`… S.R.L.zip` must not be read as base `… S.R` — the `with_suffix` trap."""
    legacy = tmp_path / "vechi"
    base = legacy / "2026-06-17_1882_Miele Appliances S.R.L"
    _write_zip(base, _invoice_xml())
    base.with_name(base.name + ".pdf").write_bytes(b"%PDF-1.4 fake")
    state = Archive.open(tmp_path / "state.db")

    run_backfill(legacy, _config(tmp_path), state)

    entry = state.catalog()[0]
    assert entry.base_path.endswith("Miele Appliances S.R.L")
    assert entry.artifacts == ["zip", "pdf"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "factura-1", _invoice_xml())
    state = Archive.open(tmp_path / "state.db")

    report = run_backfill(legacy, _config(tmp_path), state, dry_run=True)

    assert report.indexed == 1  # what *would* be cataloged
    assert state.count == 0


def test_corrupt_zip_is_reported_and_the_rest_continue(tmp_path: Path) -> None:
    legacy = tmp_path / "vechi"
    (legacy).mkdir()
    (legacy / "trunchiat.zip").write_bytes(b"not a zip at all")
    _write_zip(legacy / "factura-1", _invoice_xml())
    state = Archive.open(tmp_path / "state.db")

    report = run_backfill(legacy, _config(tmp_path), state)

    assert report.indexed == 1  # the good one still landed
    assert len(report.failures) == 1
    assert not report.ok


def test_missing_folder_is_a_boundary_error(tmp_path: Path) -> None:
    state = Archive.open(tmp_path / "state.db")
    with pytest.raises(FileNotFoundError):
        run_backfill(tmp_path / "nope", _config(tmp_path), state)


def test_backfilled_rows_do_not_gate_downloads(tmp_path: Path) -> None:
    """The invariant: `is_archived` still answers only for real ANAF ids.

    A synthetic id must never make `sync` skip the genuine message.
    """
    legacy = tmp_path / "vechi"
    _write_zip(legacy / "factura-1", _invoice_xml())
    state = Archive.open(tmp_path / "state.db")

    run_backfill(legacy, _config(tmp_path), state)

    assert not state.is_archived("7537618130")  # the real id ANAF would list
    assert state.count == 1
