"""Build the template context for one downloaded e-Factura message.

Every variable the path template may reference is assembled here, from two
sources: the message-list entry and the parsed invoice view (present when the
content is a readable UBL invoice/credit note). Missing values stay ``None`` —
the template renders them as ``unknown``.

The listing entry is *usually* present but not always: ``backfill`` reads
documents straight off disk, long after ANAF stopped listing them. Those go
through :func:`project_document`, which shares this module's single parse so a
backfilled row and a synced one can never disagree on how a field is derived.
:func:`project_archived` is the third door onto the same parse — a message
already in the catalog, re-read from its own files — and it carries the listing
facts the archive kept when the listing itself went away.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import warnings
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

from anafpy.efactura import DownloadedMessage, MessageListItem
from anafpy.efactura.authoring import InvoiceDocument, Party

__all__ = [
    "DirectionLabel",
    "Projection",
    "direction_of",
    "own_side",
    "project_archived",
    "project_document",
    "project_message",
    "read_view",
]


def read_view(message: DownloadedMessage) -> InvoiceDocument | None:
    """The flat invoice view of a downloaded message, without the warning.

    anafpy reports an unreadable view twice: on ``message.view_error`` and as a
    ``UserWarning``. The warning is swallowed here because it would reach
    stderr, bypassing whichever sink :mod:`logsink` picked, and split one
    failure across two channels on exactly the scheduled runs nobody is
    watching. Callers log it themselves from ``view_error``, which outlives this
    call.

    The distinction that error draws is the point: ``None`` with no error is
    content that is not a UBL invoice at all (a ``nok`` errors file, a buyer
    message), which both callers expect and handle. ``None`` *with* one means
    anafpy's reader — lenient about everything ANAF tolerates since 0.7.0 —
    could not read a document ANAF accepted, i.e. the vendored CIUS-RO edition
    has drifted. Shared so a backfilled document and a synced one can never
    disagree about which of the two they are.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return message.view


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
    partner_name: str | None
    partner_cif: str | None


def _party_identifiers(party: Party) -> set[str]:
    """Every CIF-shaped identifier a party carries.

    CIUS-RO's BR-RO-120 identifies a party by the legal-entity ``CompanyID``
    *or any* ``PartyTaxScheme/CompanyID`` — no VAT-scheme filter — so all four
    homes are in play, and production invoices genuinely use different ones: a
    VAT-registered party fills ``vat_id``, one below the registration threshold
    fills ``tax_registration_id`` (marked ``!VAT``), and plenty carry only
    ``legal_registration_id``. ``_digits`` drops whatever is not a CIF at all,
    such as a trade-registry number (``F35/2068/2013``) filed as the legal id.
    """
    candidates = [
        party.vat_id,
        party.tax_registration_id,
        party.legal_registration_id,
        *(identifier.id for identifier in party.identifiers),
    ]
    return {digits for value in candidates if (digits := _digits(value))}


def own_side(
    view: InvoiceDocument, cifs: Iterable[str]
) -> tuple[DirectionLabel, str] | None:
    """Classify a document by which side of it the operator is on.

    Returns ``(direction, own_cif)``, or ``None`` when neither party is one of
    ``cifs`` — a document that is not this archive's to catalog.

    :func:`direction_of` reads the listing's ``tip``; a document read off disk
    has no listing entry, so its own parties are the only evidence — and the
    better one, since ``tip`` is prose and a CIF is a CIF. Self-billing (the
    same CIF on both sides) classifies as ``sent``, matching the direction the
    document was filed under.
    """
    own = {digits for cif in cifs if (digits := _digits(cif))}
    if seller := _party_identifiers(view.seller) & own:
        return "sent", next(iter(seller))
    if buyer := _party_identifiers(view.buyer) & own:
        return "received", next(iter(buyer))
    return None


def _project(
    item: MessageListItem | None,
    view: InvoiceDocument | None,
    *,
    direction: DirectionLabel | None,
) -> _Invoice:
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
            view.seller.vat_id,
            view.seller.tax_registration_id,
            item.sender_cif if item is not None else None,
        )
        buyer_name = view.buyer.name
        buyer_cif = _party_cif(
            view.buyer.vat_id, item.receiver_cif if item is not None else None
        )
        try:
            total = view.effective_totals().payable
        except Exception:  # totals are auxiliary context — never fail the archive
            total = None
    elif item is not None:
        seller_cif = _digits(item.sender_cif)
        buyer_cif = _digits(item.receiver_cif)
    # Neither a view nor a listing entry leaves every field None; backfill
    # rejects unreadable documents before they reach here.

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
        partner_name=partner_name,
        partner_cif=partner_cif,
    )


@dataclasses.dataclass(frozen=True)
class _Listing:
    """What only ANAF's message listing ever carries.

    None of it lives in the invoice document, so how much is available depends
    on which door onto the parse was used: :func:`project_message` fills it from
    the listing entry in hand, :func:`project_document` (backfill) has none of
    it, and :func:`project_archived` recovers everything the catalog kept —
    everything but ``request_id``, which no artifact records.
    """

    message_id: str | None = None
    request_id: str | None = None
    message_type: str | None = None
    #: ANAF's ``data_creare``, already parsed (see :func:`_parse_created`).
    created: dt.datetime | None = None


@dataclasses.dataclass(frozen=True)
class Projection:
    """The two derived views of one message, from a single parse."""

    #: The full variable set available to the path template.
    context: dict[str, Any]
    #: The catalog-tier columns, keyed as ``state.CatalogEntry`` names them.
    catalog: dict[str, Any]


def project_message(
    item: MessageListItem,
    view: InvoiceDocument | None,
    *,
    cif: str,
) -> Projection:
    """Project one message into its template context and catalog columns.

    One parse feeds both, so the archive path and the catalog can never drift
    on how a field is derived. Everything is best-effort/``None`` throughout —
    the template renders missing values as ``unknown``.

    The context is deliberately narrower than the projection. ``total`` is not
    in it because a path *names* a document and an amount is a fact about it —
    one ANAF restates and the archive path would move; the catalog keeps it
    (narrowed from ``Decimal`` to ``float`` for the ``REAL`` column).
    ``seller_*``/``buyer_*`` appear nowhere: they are the same two parties as
    ``partner_*`` and ``cif`` addressed by role instead of by relationship —
    under ``direction = "both"`` a ``{seller_name}`` template files the user's
    own company as the folder for every invoice they sent, while
    ``partner_name`` is correct in both directions by construction.

    Args:
        item: the message-list entry the download originated from.
        view: the parsed flat invoice, when the content was readable UBL.
        cif: the CIF this sync run queried (the "own" company).
    """
    return _assemble(
        _project(item, view, direction=direction_of(item)),
        _Listing(
            message_id=item.id,
            request_id=item.request_id,
            message_type=item.message_type,
            created=_parse_created(item.created_at),
        ),
        cif=cif,
    )


def project_document(
    view: InvoiceDocument,
    *,
    cif: str,
    direction: DirectionLabel,
) -> Projection:
    """Project a document read off disk — no listing entry behind it.

    The backfill counterpart to :func:`project_message`, sharing its parse so
    an imported row and a synced one derive every field identically. What ANAF's
    listing alone carries is unrecoverable and stays ``None``: ``message_id``,
    ``request_id``, ``message_type``, and ``created`` (``data_creare``, which is
    the SPV indexing time and lives nowhere in the document).

    Args:
        view: the parsed flat invoice — required here, unlike the message path.
        cif: the operator's own CIF for this document (see :func:`own_side`).
        direction: which side of it the operator is on (see :func:`own_side`).
    """
    return _assemble(_project(None, view, direction=direction), _Listing(), cif=cif)


def project_archived(
    view: InvoiceDocument,
    *,
    cif: str,
    direction: DirectionLabel,
    message_id: str,
    message_type: str | None = None,
    created: dt.datetime | None = None,
) -> Projection:
    """Re-project a message already in the catalog, from its own stored files.

    The reprocess counterpart to :func:`project_message`: same parse, same
    layout, but the listing entry it was first projected from is long gone, so
    the facts only that entry carried come back from the catalog row instead
    (:mod:`reprocess` passes them). ``request_id`` is the exception — nothing
    the archive writes records it, so it stays ``None`` here, and a path
    template that references it cannot be re-rendered.

    Args:
        view: the parsed flat invoice, re-read from disk.
        cif: the row's own CIF — the sync run's, not re-derived.
        direction: the row's direction, classified from ANAF's ``tip`` at
            download time and better evidence than anything derivable now.
        message_id: ANAF's message id, straight from the row.
        message_type: the row's ``tip``, when it has one.
        created: the row's ``created_at`` (ANAF's ``data_creare``).
    """
    return _assemble(
        _project(None, view, direction=direction),
        _Listing(message_id=message_id, message_type=message_type, created=created),
        cif=cif,
    )


def _assemble(inv: _Invoice, listing: _Listing, *, cif: str) -> Projection:
    """Lay one projected invoice out as template context + catalog columns."""
    created = listing.created
    context = {
        "message_id": listing.message_id,
        "request_id": listing.request_id,
        "message_type": listing.message_type,
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
    catalog = {
        "issue_date": inv.issue_date,
        "number": inv.number,
        "partner_name": inv.partner_name,
        "partner_cif": inv.partner_cif,
        "total": float(inv.total) if inv.total is not None else None,
        "currency": inv.currency,
        "message_type": listing.message_type,
        "created_at": created,
    }
    return Projection(context=context, catalog=catalog)
