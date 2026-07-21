"""Template-context assembly from list items and parsed invoices."""

import datetime as dt

from anafpy.efactura import MessageListItem
from anafpy.efactura.authoring import InvoiceDocument
from anafpy.efactura.authoring.models import Party, Seller

from anaf_sync.context import build_context, catalog_fields, direction_of


def _item(**overrides: str) -> MessageListItem:
    defaults: dict[str, str] = {
        "id": "3001",
        "request_id": "5001",
        "message_type": "FACTURA PRIMITA",
        "created_at": "202607181430",
        "cif": "111",
        "details": "Factura cu id_incarcare=5001 emisa de cif_emitent=222 "
        "pentru cif_beneficiar=111",
    }
    defaults.update(overrides)
    return MessageListItem(**defaults)  # type: ignore[arg-type]


def test_direction_classification() -> None:
    assert direction_of(_item(message_type="FACTURA PRIMITA")) == "received"
    assert direction_of(_item(message_type="FACTURA TRIMISA")) == "sent"
    assert direction_of(_item(message_type="ERORI FACTURA")) is None
    assert direction_of(_item(message_type="MESAJ CUMPARATOR")) is None


def test_context_without_a_parsed_view_falls_back_to_the_listing() -> None:
    context = build_context(_item(), None, cif="RO111")
    assert context["message_id"] == "3001"
    assert context["direction"] == "received"
    assert context["cif"] == "111"
    assert context["created"] == dt.datetime(2026, 7, 18, 14, 30)
    assert context["created_month"] == "iulie"
    assert context["issue_month"] is None  # no parsed view → no issue date
    # CIFs extracted by anafpy from the `detalii` prose.
    assert context["partner_cif"] == "222"  # received → partner is the seller
    assert context["number"] is None


def test_context_with_a_parsed_view() -> None:
    # model_construct: skip UBL validation — only the projected fields matter here.
    view = InvoiceDocument.model_construct(
        number="FCT-100",
        issue_date=dt.date(2026, 7, 1),
        currency="RON",
        seller=Seller.model_construct(name="Furnizor SRL", vat_id="RO222"),
        buyer=Party.model_construct(name="Client SRL", vat_id="RO111"),
    )
    context = build_context(_item(), view, cif="111")
    assert context["number"] == "FCT-100"
    assert context["issue_date"] == dt.date(2026, 7, 1)
    assert context["issue_month"] == "iulie"
    assert context["kind"] == "invoice"
    assert context["partner_name"] == "Furnizor SRL"
    assert context["partner_cif"] == "222"


def test_path_context_omits_catalog_only_fields() -> None:
    """`total` and the role-addressed parties are catalog data, not path data.

    They still exist on the projection — `catalog_fields` exports `total`, and
    seller/buyer are what `partner_*` is derived from — but a path template can
    no longer reference them (see `build_context`).
    """
    context = build_context(_item(), None, cif="111")
    for name in ("total", "seller_name", "seller_cif", "buyer_name", "buyer_cif"):
        assert name not in context, name
    assert "total" in catalog_fields(_item(), None)


def test_unusable_totals_do_not_break_the_projection() -> None:
    # model_construct skips validation, so effective_totals() raises on this
    # stub. A total is auxiliary context and must never fail an archive run.
    view = InvoiceDocument.model_construct(
        number="FCT-100",
        issue_date=dt.date(2026, 7, 1),
        currency="RON",
        seller=Seller.model_construct(name="Furnizor SRL", vat_id="RO222"),
        buyer=Party.model_construct(name="Client SRL", vat_id="RO111"),
    )
    assert catalog_fields(_item(), view)["total"] is None


def test_sent_invoice_partner_is_the_buyer() -> None:
    item = _item(
        message_type="FACTURA TRIMISA",
        details="Factura cu id_incarcare=5001 emisa de cif_emitent=111 "
        "pentru cif_beneficiar=333",
    )
    context = build_context(item, None, cif="111")
    assert context["direction"] == "sent"
    assert context["partner_cif"] == "333"
