"""Pure Romanian money/date formatting (no Qt)."""

import datetime as dt

from anaf_sync.tray.format import archived_at, money, provenance, short_date


def test_money_romanian_grouping_and_currency() -> None:
    assert money(4821.50, "RON") == "4.821,50 RON"
    assert money(1245.00, "RON") == "1.245,00 RON"
    assert money(386.75, "RON") == "386,75 RON"
    assert money(12400.00, "RON") == "12.400,00 RON"


def test_money_without_currency_or_value() -> None:
    assert money(100.0, None) == "100,00"
    assert money(None, "RON") == "—"


def test_short_date() -> None:
    assert short_date(dt.date(2026, 7, 18)) == "18 iul."
    assert short_date(dt.date(2026, 1, 3)) == "3 ian."
    assert short_date(None) == "—"


def test_archived_at_is_local_and_tabular() -> None:
    when = dt.datetime(2026, 7, 18, 14, 32, tzinfo=dt.UTC)
    rendered = archived_at(when)
    assert "2026" in rendered and ", " in rendered
    assert archived_at(None) == "—"


def test_provenance_passthrough_or_dash() -> None:
    assert provenance("3210447810") == "3210447810"
    assert provenance(None) == "—"
