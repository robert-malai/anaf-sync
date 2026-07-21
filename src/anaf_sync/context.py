"""Build the template context for one downloaded e-Factura message.

Every variable the path template may reference is assembled here, from two
sources: the message-list entry (always present) and the parsed invoice view
(present when the downloaded content is a readable UBL invoice/credit note).
Missing values stay ``None`` — the template renders them as ``unknown``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Any

from anafpy.efactura import MessageListItem
from anafpy.efactura.authoring import InvoiceDocument

__all__ = ["DirectionLabel", "build_context", "catalog_fields", "direction_of"]

#: "received" | "sent" — narrow alias for readability. Deliberately not the
#: config.Direction enum: that one is the *filter* the user configures (and
#: includes "both"), this is the classification of a single message.
DirectionLabel = str

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


def direction_of(item: MessageListItem) -> DirectionLabel | None:
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


@dataclasses.dataclass(frozen=True)
class _Invoice:
    """The invoice fields projected from one message + optional UBL view.

    The single parsing choke point behind both the path context and the
    catalog projection, so the two can never drift on how a field is derived.
    """

    direction: DirectionLabel | None
    number: str | None
    issue_date: dt.date | None
    due_date: dt.date | None
    currency: str | None
    total: Decimal | None
    kind: str | None
    seller_name: str | None
    seller_cif: str | None
    buyer_name: str | None
    buyer_cif: str | None
    partner_name: str | None
    partner_cif: str | None


def _project(item: MessageListItem, view: InvoiceDocument | None) -> _Invoice:
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

    direction = direction_of(item)
    if direction == "sent":
        partner_name, partner_cif = buyer_name, buyer_cif
    else:
        # Received — and the safe default when the type is unrecognised.
        partner_name, partner_cif = seller_name, seller_cif

    return _Invoice(
        direction=direction,
        number=number,
        issue_date=issue_date,
        due_date=due_date,
        currency=currency,
        total=total,
        kind=kind,
        seller_name=seller_name,
        seller_cif=seller_cif,
        buyer_name=buyer_name,
        buyer_cif=buyer_cif,
        partner_name=partner_name,
        partner_cif=partner_cif,
    )


def build_context(
    item: MessageListItem,
    view: InvoiceDocument | None,
    *,
    cif: str,
) -> dict[str, Any]:
    """The full variable set available to the path template.

    Deliberately narrower than :class:`_Invoice`. ``total`` is not here because a
    path *names* a document and an amount is a fact about it — one ANAF restates
    and the archive path would move. ``seller_*``/``buyer_*`` are not here
    because they are the same two parties as ``partner_*`` and ``cif`` addressed
    by role instead of by relationship: under ``direction = "both"`` a
    ``{seller_name}`` template files the user's own company as the folder for
    every invoice they sent, while ``partner_name`` is correct in both
    directions by construction. Both still exist on :class:`_Invoice` — they
    feed ``partner_*`` and :func:`catalog_fields`.

    Args:
        item: the message-list entry the download originated from.
        view: the parsed flat invoice, when the content was readable UBL.
        cif: the CIF this sync run queried (the "own" company).
    """
    inv = _project(item, view)
    created = _parse_created(item.created_at)
    return {
        "message_id": item.id,
        "request_id": item.request_id,
        "message_type": item.message_type,
        "created": created,
        "created_month": _ro_month(created),
        "cif": _digits(cif) or cif,
        "direction": inv.direction,
        "number": inv.number,
        "issue_date": inv.issue_date,
        "issue_month": _ro_month(inv.issue_date),
        "due_date": inv.due_date,
        "currency": inv.currency,
        "kind": inv.kind,
        "partner_name": inv.partner_name,
        "partner_cif": inv.partner_cif,
    }


def catalog_fields(
    item: MessageListItem, view: InvoiceDocument | None
) -> dict[str, Any]:
    """The catalog-tier columns for one message (see ``state.CatalogEntry``).

    Best-effort/``None`` throughout, projected from the same sources as
    :func:`build_context`. ``total`` is narrowed from ``Decimal`` to ``float``
    for the catalog's ``REAL`` column. ``created`` is ANAF's ``data_creare``
    (when the message entered SPV), keyed as the engine maps it onto
    ``CatalogEntry.created_at``.
    """
    inv = _project(item, view)
    return {
        "issue_date": inv.issue_date,
        "number": inv.number,
        "partner_name": inv.partner_name,
        "partner_cif": inv.partner_cif,
        "total": float(inv.total) if inv.total is not None else None,
        "currency": inv.currency,
        "message_type": item.message_type,
        "created": _parse_created(item.created_at),
    }
