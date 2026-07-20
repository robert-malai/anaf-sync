"""Build the template context for one downloaded e-Factura message.

Every variable the path template may reference is assembled here, from two
sources: the message-list entry (always present) and the parsed invoice view
(present when the downloaded content is a readable UBL invoice/credit note).
Missing values stay ``None`` — the template renders them as ``unknown``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from anafpy.efactura import MessageListItem
from anafpy.efactura.authoring import InvoiceDocument

__all__ = ["Direction", "build_context", "direction_of"]

Direction = str  # "received" | "sent" | None — narrow alias for readability

#: Romanian month names (lowercase, as the language convention dictates).
#: Deliberately hardcoded — ``%B`` depends on the process locale, which would
#: make archive paths differ between machines. All names are ASCII, so they
#: pass path sanitisation untouched. Case is the template's job (``!u``/``!c``).
_RO_MONTHS = (
    "ianuarie",
    "februarie",
    "martie",
    "aprilie",
    "mai",
    "iunie",
    "iulie",
    "august",
    "septembrie",
    "octombrie",
    "noiembrie",
    "decembrie",
)


def _ro_month(value: dt.date | dt.datetime | None) -> str | None:
    return _RO_MONTHS[value.month - 1] if value is not None else None


def direction_of(item: MessageListItem) -> Direction | None:
    """Classify a message as received/sent from ANAF's ``tip`` field."""
    kind = (item.message_type or "").casefold()
    if "primita" in kind:
        return "received"
    if "trimisa" in kind:
        return "sent"
    return None


def _parse_created(raw: str | None) -> dt.datetime | None:
    """ANAF's ``data_creare`` is ``yyyymmddhhmm``; be lenient about it."""
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw.strip(), "%Y%m%d%H%M")
    except ValueError:
        return None


def _digits(value: str | None) -> str | None:
    """A CIF as plain digits, from either ``123`` or ``RO123`` shapes."""
    if not value:
        return None
    cleaned = value.strip().upper().removeprefix("RO")
    return cleaned if cleaned.isdigit() else None


def _party_cif(vat_id: str | None, *fallbacks: str | None) -> str | None:
    for candidate in (vat_id, *fallbacks):
        if digits := _digits(candidate):
            return digits
    return None


def build_context(
    item: MessageListItem,
    view: InvoiceDocument | None,
    *,
    cif: str,
) -> dict[str, Any]:
    """The full variable set available to the path template.

    Args:
        item: the message-list entry the download originated from.
        view: the parsed flat invoice, when the content was readable UBL.
        cif: the CIF this sync run queried (the "own" company).
    """
    direction = direction_of(item)

    number: str | None = None
    issue_date: dt.date | None = None
    due_date: dt.date | None = None
    currency: str | None = None
    total: Decimal | None = None
    kind: str | None = None
    seller_name: str | None = None
    seller_cif: str | None = None
    buyer_name: str | None = None
    buyer_cif: str | None = None

    if view is not None:
        number = view.number
        issue_date = view.issue_date
        due_date = view.due_date
        currency = str(view.currency)
        kind = view.kind.value
        seller_name = view.seller.name
        seller_cif = _party_cif(
            view.seller.vat_id, view.seller.tax_registration_id, item.sender_cif
        )
        buyer_name = view.buyer.name
        buyer_cif = _party_cif(view.buyer.vat_id, item.receiver_cif)
        try:
            total = view.effective_totals().payable
        except Exception:  # totals are auxiliary context — never fail the archive
            total = None
    else:
        seller_cif = _digits(item.sender_cif)
        buyer_cif = _digits(item.receiver_cif)

    if direction == "sent":
        partner_name, partner_cif = buyer_name, buyer_cif
    else:
        # Received — and the safe default when the type is unrecognised.
        partner_name, partner_cif = seller_name, seller_cif

    created = _parse_created(item.created_at)
    return {
        "message_id": item.id,
        "request_id": item.request_id,
        "message_type": item.message_type,
        "created": created,
        "created_month": _ro_month(created),
        "cif": _digits(cif) or cif,
        "direction": direction,
        "number": number,
        "issue_date": issue_date,
        "issue_month": _ro_month(issue_date),
        "due_date": due_date,
        "currency": currency,
        "total": total,
        "kind": kind,
        "seller_name": seller_name,
        "seller_cif": seller_cif,
        "buyer_name": buyer_name,
        "buyer_cif": buyer_cif,
        "partner_name": partner_name,
        "partner_cif": partner_cif,
    }
