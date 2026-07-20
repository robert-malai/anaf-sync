"""The handoff's §Sample Data, built into a real archive for the M2 tests.

One builder, ``seed_sample_archive``, produces exactly the six rows the mockup
shows (`#1b`): a failing message pinned on top, an amber delayed invoice
(FF-88214, issued 11 iul., uploaded 19 iul. → 8 days), and four normal rows.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from anaf_sync.state import Archive, CatalogEntry


def _entry(
    message_id: str,
    *,
    direction: str,
    number: str,
    partner: str,
    issue: dt.date,
    created: dt.datetime | None,
    total: float,
) -> CatalogEntry:
    return CatalogEntry(
        message_id=message_id,
        cif="12345678",
        direction=direction,
        base_path=f"/archive/{message_id}",
        artifacts=["zip", "pdf"],
        issue_date=issue,
        number=number,
        partner_name=partner,
        partner_cif="87654321",
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
                direction="sent",
                number="AS-1042",
                partner="MOBILA PRODEX S.R.L.",
                issue=dt.date(2026, 7, 15),
                created=dt.datetime(2026, 7, 16, 9, 0),
                total=12400.00,
            )
        )
        # Delayed: issued 11 iul., uploaded 19 iul. → 8 days (> 5 threshold).
        archive.record(
            _entry(
                "3210447814",
                direction="received",
                number="FF-88214",
                partner="BIROTICA PLUS S.R.L.",
                issue=dt.date(2026, 7, 11),
                created=dt.datetime(2026, 7, 19, 9, 0),
                total=386.75,
            )
        )
        archive.record(
            _entry(
                "3210447815",
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
