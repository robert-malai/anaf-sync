"""Pure Romanian rendering: money/dates, pluralisation, relative time (no Qt)."""

import datetime as dt

from anaf_sync.tray.format import (
    archived_at,
    direction_label,
    money,
    noun,
    provenance,
    relative_time,
    short_date,
    spv_expiry,
)

_NOW = dt.datetime(2026, 7, 20, 14, 32)


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


def test_noun_romanian_de_rule() -> None:
    assert noun(1, "factură", "facturi") == "1 factură"
    assert noun(3, "factură", "facturi") == "3 facturi"
    assert noun(21, "factură", "facturi") == "21 de facturi"
    assert noun(101, "zi", "zile") == "101 zile"  # 100-rule: 101–119 drop "de"


def test_relative_time_just_now() -> None:
    assert relative_time(_NOW - dt.timedelta(seconds=20), _NOW) == "chiar acum"


def test_relative_time_minutes() -> None:
    assert relative_time(_NOW - dt.timedelta(minutes=1), _NOW) == "acum 1 minut"
    assert relative_time(_NOW - dt.timedelta(minutes=5), _NOW) == "acum 5 minute"
    assert relative_time(_NOW - dt.timedelta(minutes=25), _NOW) == "acum 25 de minute"


def test_relative_time_hours_same_day() -> None:
    assert relative_time(_NOW - dt.timedelta(hours=2), _NOW) == "acum 2 ore"
    assert relative_time(_NOW - dt.timedelta(hours=1), _NOW) == "acum 1 oră"


def test_relative_time_yesterday_shows_clock() -> None:
    yesterday = dt.datetime(2026, 7, 19, 14, 32)
    assert relative_time(yesterday, _NOW) == "ieri, 14:32"


def test_relative_time_within_week() -> None:
    assert relative_time(dt.datetime(2026, 7, 17, 9, 0), _NOW) == "acum 3 zile"


def test_relative_time_older_is_short_date() -> None:
    assert relative_time(dt.datetime(2026, 7, 3, 9, 0), _NOW) == "3 iul."


def test_spv_expiry_phrases() -> None:
    assert spv_expiry(9) == "expiră din SPV în 9 zile"
    assert spv_expiry(1) == "expiră din SPV în 1 zi"
    assert spv_expiry(0) == "a expirat din SPV"


def test_direction_label_maps_known_and_unknown() -> None:
    assert direction_label("received") == "primită"
    assert direction_label("sent") == "trimisă"
    assert direction_label("failing") == "eșuată"
    assert direction_label("altceva") == ""
