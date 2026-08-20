"""The handoff's §Sample Data, built into a real archive for the M2 tests.

One builder, ``seed_sample_archive``, produces exactly the six rows the mockup
shows (`#1b`): a failing message pinned on top, an amber delayed invoice
(FF-88214, issued Saturday 11 iul., uploaded Monday 20 iul. → 6 working days),
and four normal rows.

The rows span **two followed CIFs and both directions** on purpose. AS-1042 is
the one sent invoice, so it is the row where the followed CIF sits in *De la*
and the partner's in *Pentru* — the mirror of every other row, and the only way
a test of the two role columns can tell the rule from a coincidence.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from anaf_sync.state import Archive, CatalogEntry

#: The two CIFs the sample archive follows, matching the Setări mockup.
OWN_CIFS = ("12345678", "87654321")


def _entry(
    message_id: str,
    *,
    direction: str,
    number: str,
    partner: str,
    issue: dt.date,
    created: dt.datetime | None,
    total: float,
    cif: str = OWN_CIFS[0],
    partner_cif: str = "14338501",
) -> CatalogEntry:
    return CatalogEntry(
        message_id=message_id,
        cif=cif,
        direction=direction,
        base_path=f"/archive/{message_id}",
        artifacts=["zip", "pdf"],
        issue_date=issue,
        number=number,
        partner_name=partner,
        partner_cif=partner_cif,
        total=total,
        currency="RON",
        message_type=(
            "FACTURA PRIMITA" if direction == "received" else "FACTURA TRIMISA"
        ),
        created_at=created,
    )


def seed_sample_archive(path: Path) -> None:
    """Populate ``path`` with the handoff's sample catalog + one failing message."""
    with Archive.open(path) as archive:
        archive.record(
            _entry(
                "3210447811",
                partner_cif="14338501",
                direction="received",
                number="FCT-2107",
                partner="ELECTROMONTAJ CARPAȚI S.R.L.",
                issue=dt.date(2026, 7, 18),
                created=dt.datetime(2026, 7, 18, 9, 0),
                total=4821.50,
            )
        )
        archive.record(
            _entry(
                "3210447812",
                cif=OWN_CIFS[1],
                partner_cif="11694562",
                direction="received",
                number="2026-0713",
                partner="DISTRIGAZ VEST S.A.",
                issue=dt.date(2026, 7, 17),
                created=dt.datetime(2026, 7, 17, 9, 0),
                total=1245.00,
            )
        )
        archive.record(
            _entry(
                "3210447813",
                partner_cif="22518743",
                direction="sent",
                number="AS-1042",
                partner="MOBILA PRODEX S.R.L.",
                issue=dt.date(2026, 7, 15),
                created=dt.datetime(2026, 7, 16, 9, 0),
                total=12400.00,
            )
        )
        # Delayed: issued Saturday 11 iul., uploaded Monday 20 iul. →
        # 6 working days (> the 5-working-day threshold).
        archive.record(
            _entry(
                "3210447814",
                partner_cif="31274865",
                direction="received",
                number="FF-88214",
                partner="BIROTICA PLUS S.R.L.",
                issue=dt.date(2026, 7, 11),
                created=dt.datetime(2026, 7, 20, 9, 0),
                total=386.75,
            )
        )
        archive.record(
            _entry(
                "3210447815",
                cif=OWN_CIFS[1],
                partner_cif="18293401",
                direction="received",
                number="FCT-1001",
                partner="ACME CONSTRUCT S.R.L.",
                issue=dt.date(2026, 7, 3),
                created=dt.datetime(2026, 7, 6, 9, 0),
                total=2480.00,
            )
        )
        # The failing message pinned on top: TERMOENERGIA, HTTP 500.
        archive.record_failure("3210447810", "HTTP 500")
