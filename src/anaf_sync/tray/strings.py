"""Every operator-facing string the tray shows, in Romanian with diacritics.

The single home for UI copy (the plan forbids Romanian strings anywhere else in
the tray package). Transcribed verbatim from the design handoff §1. Code
identifiers — ``anafpy auth login``, ``config.toml``, ``{cif}`` — are never
translated. Relative-time and Romanian pluralisation live here too, pure and
unit-tested.
"""

from __future__ import annotations

import datetime as dt

__all__ = [
    "ARCHIVE_UP_TO_DATE",
    "AUTH_EXPIRED_PREFIX",
    "AUTH_LOGIN_COMMAND",
    "MENU_ARCHIVED_INVOICES",
    "MENU_OPEN_FOLDER",
    "MENU_QUIT",
    "MENU_SETTINGS",
    "MENU_SYNC_NOW",
    "MENU_SYNCING",
    "NEEDS_ATTENTION",
    "SYNC_BROKEN",
    "failing_alert",
    "generic_error_alert",
    "last_sync_subline",
    "last_success_subline",
    "never_synced_subline",
    "new_invoices_phrase",
    "relative_time",
]

# -- Headlines (handoff §1) ---------------------------------------------------

ARCHIVE_UP_TO_DATE = "Arhiva este la zi"
NEEDS_ATTENTION = "Necesită atenție"
SYNC_BROKEN = "Sincronizarea nu funcționează"

# -- Alert rows ---------------------------------------------------------------

#: Rendered before the mono ``anafpy auth login`` chip in the red alert row.
AUTH_EXPIRED_PREFIX = "Autentificarea ANAF a expirat — rulați "
AUTH_LOGIN_COMMAND = "anafpy auth login"

# -- Menu items (handoff §1) --------------------------------------------------

MENU_SYNC_NOW = "Sincronizează acum"
MENU_SYNCING = "Se sincronizează…"
MENU_ARCHIVED_INVOICES = "Facturi arhivate…"
MENU_OPEN_FOLDER = "Deschide dosarul arhivei"
MENU_SETTINGS = "Setări…"
MENU_QUIT = "Ieșire"

#: Abbreviated Romanian month names (``context._RO_MONTHS`` is the full form).
#: Reused by the M2 formatter; kept here because relative-time needs them.
MONTHS_ABBR = (
    "ian.",
    "feb.",
    "mar.",
    "apr.",
    "mai",
    "iun.",
    "iul.",
    "aug.",
    "sept.",
    "oct.",
    "nov.",
    "dec.",
)


def _needs_de(n: int) -> bool:
    """Romanian inserts ``de`` before the noun for 0 and 20+ (per the 100-rule)."""
    remainder = n % 100
    return n >= 20 and (remainder == 0 or remainder >= 20)


def _noun(n: int, singular: str, plural: str) -> str:
    """``"1 factură"`` / ``"3 facturi"`` / ``"21 de facturi"``."""
    if n == 1:
        return f"{n} {singular}"
    if _needs_de(n):
        return f"{n} de {plural}"
    return f"{n} {plural}"


def new_invoices_phrase(count: int) -> str:
    """``"3 facturi noi"`` / ``"1 factură nouă"`` (agreeing adjective)."""
    adjective = "nouă" if count == 1 else "noi"
    return f"{_noun(count, 'factură', 'facturi')} {adjective}"


def relative_time(when: dt.datetime, now: dt.datetime) -> str:
    """A human, Romanian relative time: ``"acum 2 ore"``, ``"ieri, 14:32"``.

    ``when`` and ``now`` must share awareness (both naive or both aware); the
    caller converts UTC timestamps to local time first, so ``%H:%M`` reads as
    the wall clock the user knows.
    """
    delta = now - when
    seconds = delta.total_seconds()
    if seconds < 60:
        return "chiar acum"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"acum {_noun(minutes, 'minut', 'minute')}"
    day_gap = (now.date() - when.date()).days
    if day_gap == 0:
        hours = int(seconds // 3600)
        return f"acum {_noun(hours, 'oră', 'ore')}"
    if day_gap == 1:
        return f"ieri, {when:%H:%M}"
    if day_gap <= 6:
        return f"acum {_noun(day_gap, 'zi', 'zile')}"
    return f"{when.day} {MONTHS_ABBR[when.month - 1]}"


# -- Sublines -----------------------------------------------------------------


def last_sync_subline(relative: str, new_count: int) -> str:
    """``"Ultima sincronizare: acum 2 ore · 3 facturi noi"`` (suffix if any new)."""
    base = f"Ultima sincronizare: {relative}"
    if new_count > 0:
        return f"{base} · {new_invoices_phrase(new_count)}"
    return base


def last_success_subline(relative: str) -> str:
    """``"Ultima sincronizare reușită: ieri, 14:32"`` (the error-state subline)."""
    return f"Ultima sincronizare reușită: {relative}"


def never_synced_subline() -> str:
    return "Nu s-a sincronizat încă"


# -- Alert bodies -------------------------------------------------------------


def _spv_expiry(days_left: int) -> str:
    if days_left <= 0:
        return "a expirat din SPV"
    return f"expiră din SPV în {_noun(days_left, 'zi', 'zile')}"


def failing_alert(count: int, partner: str | None, days_left: int) -> str:
    """Amber row: ``"1 factură eșuează repetat — TERMOENERGIA S.R.L. — …"``."""
    parts = [f"{_noun(count, 'factură', 'facturi')} eșuează repetat"]
    if partner:
        parts.append(partner)
    parts.append(_spv_expiry(days_left))
    return " — ".join(parts)


def generic_error_alert() -> str:
    """Red row when the last run broke for a non-auth reason (crash / stale)."""
    return "Ultima sincronizare a eșuat — verificați jurnalul aplicației"


# -- Main window (handoff §2) -------------------------------------------------

WINDOW_TITLE = "anaf-sync"
SIDEBAR_INVOICES = "Facturi"
SIDEBAR_SETTINGS = "Setări"

SEARCH_PLACEHOLDER = "Caută după număr sau partener…"

FILTER_ALL = "Toate"
FILTER_RECEIVED = "Primite"
FILTER_SENT = "Trimise"
FILTER_PROBLEMS = "Probleme"

PERIOD_LABEL = "Perioadă"
PERIOD_CURRENT = "Luna curentă"
PERIOD_ALL = "Toate"
PERIOD_CUSTOM = "Personalizat…"

# Table columns (uppercased by the header delegate/stylesheet, stored as-is).
COL_DATE = "Data"
COL_NUMBER = "Număr"
COL_PARTNER = "Partener"
COL_DIRECTION = "Direcție"
COL_TOTAL = "Total"

# Direction pills.
PILL_RECEIVED = "primită"
PILL_SENT = "trimisă"
PILL_FAILED = "eșuată"

# Details pane — key-fact labels.
DETAIL_PARTNER = "Partener"
DETAIL_PARTNER_CIF = "CIF partener"
DETAIL_ISSUE_DATE = "Data emiterii"
DETAIL_SPV_DATE = "Încărcată în SPV"
DETAIL_TOTAL = "Total"
DETAIL_FILES = "Fișiere pe disc"
DETAIL_PATH = "Cale în arhivă"
BTN_OPEN_PDF = "Deschide PDF"
BTN_REVEAL = "Arată în dosar"
BTN_RETRY = "Reîncearcă acum"
TOOLTIP_FILE_MISSING = "fișierul nu a fost găsit pe disc"

# Provenance block.
DETAIL_MESSAGE_ID = "message_id"
DETAIL_MESSAGE_TYPE = "tip mesaj"
DETAIL_ARCHIVED_AT = "arhivat la"

# Delayed / failing detail panels.
DELAYED_TITLE = "Declarată cu întârziere"
FAILING_TITLE = "Descărcarea eșuează repetat"
FAILING_LAST_ERROR = "Ultima eroare:"
DETAILS_EMPTY = "Selectați o factură pentru detalii."


def problems_chip(count: int) -> str:
    """``"Probleme"`` / ``"Probleme (1)"`` — suffix the count when non-zero."""
    return FILTER_PROBLEMS if count == 0 else f"{FILTER_PROBLEMS} ({count})"


def footer_text(shown: int, total: int) -> str:
    """``"12 afișate · 128 în arhivă · lista se încarcă pe măsură ce derulați"``."""
    return (
        f"{shown} afișate · {total} în arhivă · "
        "lista se încarcă pe măsură ce derulați"
    )


def delayed_body(issue: str, spv: str, delay_days: int, threshold_days: int) -> str:
    """``"Emisă 11 iul. · încărcată în SPV 19 iul. — după 8 zile (limita: 5 zile)"``."""
    after = _noun(delay_days, "zi", "zile")
    limit = _noun(threshold_days, "zi", "zile")
    return f"Emisă {issue} · încărcată în SPV {spv} — după {after} (limita: {limit})"


def failing_since(first: str, attempts: int) -> str:
    """``"Eșuează din 11 iul. · 6 încercări"``."""
    return f"Eșuează din {first} · {_noun(attempts, 'încercare', 'încercări')}"


def spv_expiry_line(days_left: int) -> str:
    """``"Expiră din SPV în 9 zile"`` (capitalised, for the failing panel)."""
    return _spv_expiry(days_left).capitalize()
