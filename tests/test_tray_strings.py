"""Pure RO copy + relative-time + pluralisation (no Qt)."""

import datetime as dt

from anaf_sync.tray import strings

_NOW = dt.datetime(2026, 7, 20, 14, 32)


def test_relative_time_just_now() -> None:
    assert strings.relative_time(_NOW - dt.timedelta(seconds=20), _NOW) == "chiar acum"


def test_relative_time_minutes() -> None:
    assert strings.relative_time(_NOW - dt.timedelta(minutes=1), _NOW) == "acum 1 minut"
    assert (
        strings.relative_time(_NOW - dt.timedelta(minutes=5), _NOW) == "acum 5 minute"
    )
    assert (
        strings.relative_time(_NOW - dt.timedelta(minutes=25), _NOW)
        == "acum 25 de minute"
    )


def test_relative_time_hours_same_day() -> None:
    assert strings.relative_time(_NOW - dt.timedelta(hours=2), _NOW) == "acum 2 ore"
    assert strings.relative_time(_NOW - dt.timedelta(hours=1), _NOW) == "acum 1 oră"


def test_relative_time_yesterday_shows_clock() -> None:
    yesterday = dt.datetime(2026, 7, 19, 14, 32)
    assert strings.relative_time(yesterday, _NOW) == "ieri, 14:32"


def test_relative_time_within_week() -> None:
    assert strings.relative_time(dt.datetime(2026, 7, 17, 9, 0), _NOW) == "acum 3 zile"


def test_relative_time_older_is_short_date() -> None:
    assert strings.relative_time(dt.datetime(2026, 7, 3, 9, 0), _NOW) == "3 iul."


def test_new_invoices_phrase_agrees_in_number() -> None:
    assert strings.new_invoices_phrase(1) == "1 factură nouă"
    assert strings.new_invoices_phrase(3) == "3 facturi noi"
    assert strings.new_invoices_phrase(21) == "21 de facturi noi"


def test_last_sync_subline_appends_new_only_when_present() -> None:
    assert (
        strings.last_sync_subline("acum 2 ore", 0) == "Ultima sincronizare: acum 2 ore"
    )
    assert (
        strings.last_sync_subline("acum 2 ore", 3)
        == "Ultima sincronizare: acum 2 ore · 3 facturi noi"
    )


def test_last_success_subline() -> None:
    assert (
        strings.last_success_subline("ieri, 14:32")
        == "Ultima sincronizare reușită: ieri, 14:32"
    )


def test_failing_alert_matches_handoff() -> None:
    alert = strings.failing_alert(1, "TERMOENERGIA S.R.L.", 9)
    assert (
        alert
        == "1 factură eșuează repetat — TERMOENERGIA S.R.L. — expiră din SPV în 9 zile"
    )


def test_failing_alert_without_partner_or_expired() -> None:
    assert (
        strings.failing_alert(2, None, 0)
        == "2 facturi eșuează repetat — a expirat din SPV"
    )


def test_auth_alert_keeps_command_untranslated() -> None:
    assert strings.AUTH_LOGIN_COMMAND == "anafpy auth login"
    assert strings.AUTH_EXPIRED_PREFIX.startswith("Autentificarea ANAF a expirat")
