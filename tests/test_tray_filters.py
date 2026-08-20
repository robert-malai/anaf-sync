"""The filter state the header, the model and the bar all read — no display."""

from __future__ import annotations

import datetime as dt

import pytest

pytest.importorskip("PySide6")

from anaf_sync.tray.filters import (  # noqa: E402
    KEY_DELAYED,
    KEY_DIRECTIONS,
    KEY_ISSUED,
    KEY_PARTNER,
    FilterState,
)
from anaf_sync.tray.models import (  # noqa: E402
    ALL_DIRECTIONS,
    COL_DIRECTION,
    COL_ISSUED,
    COL_PARTNER,
    COL_UPLOADED,
    FAILING,
    split_direction_choice,
)
from anaf_sync.tray.period import CUSTOM, THIS_MONTH, DateSpan  # noqa: E402

_TODAY = dt.date(2026, 7, 20)


# -- the SQL half -------------------------------------------------------------


def test_empty_state_queries_nothing_and_shows_everything() -> None:
    filters = FilterState().to_filters(_TODAY)
    assert filters.query == filters.query.__class__()
    assert filters.show_failing is True
    assert filters.delayed_only is False


def test_named_spans_are_resolved_against_today() -> None:
    query = FilterState(issued=DateSpan(mode=THIS_MONTH)).to_filters(_TODAY).query
    assert query.issued_from == dt.date(2026, 7, 1)
    assert query.issued_to == dt.date(2026, 7, 31)


def test_blank_strings_become_none_rather_than_empty_filters() -> None:
    """An empty box must not become ``LIKE '%%'`` and quietly filter nothing."""
    query = FilterState(search="", partner="  ").to_filters(_TODAY).query
    assert query.search is None
    assert query.partner is None


def test_failing_never_reaches_a_where_clause() -> None:
    directions, show_failing = split_direction_choice(ALL_DIRECTIONS)
    assert directions is None  # both real directions checked = unfiltered
    assert show_failing is True

    directions, show_failing = split_direction_choice(frozenset({"received"}))
    assert directions == frozenset({"received"})
    assert show_failing is False

    directions, show_failing = split_direction_choice(frozenset({FAILING}))
    # "eșuată" alone means no real direction is wanted, which is not the same
    # as "every direction" — an empty set, not None.
    assert directions == frozenset()
    assert show_failing is True


# -- what the header paints ---------------------------------------------------


def test_active_columns_names_only_the_filtered_ones() -> None:
    assert FilterState().active_columns() == frozenset()
    state = FilterState(partner="ACME", issued=DateSpan(mode=THIS_MONTH))
    assert state.active_columns() == frozenset({COL_PARTNER, COL_ISSUED})


def test_delayed_flag_lights_the_uploaded_funnel() -> None:
    """It lives in that popover, so it must light that column even alone."""
    assert FilterState(delayed_only=True).active_columns() == frozenset({COL_UPLOADED})


def test_partial_direction_choice_lights_the_direction_funnel() -> None:
    state = FilterState(directions=frozenset({"received"}))
    assert state.active_columns() == frozenset({COL_DIRECTION})


# -- what the bar shows -------------------------------------------------------


def test_search_is_not_a_chip() -> None:
    """Its own field already shows it; a chip would say the same thing twice."""
    state = FilterState(search="ACME")
    assert state.chips(_TODAY) == []
    assert state.any_active is False


def test_chips_name_presets_but_recite_a_custom_range() -> None:
    state = FilterState(issued=DateSpan(mode=THIS_MONTH))
    assert state.chips(_TODAY)[0].label == "Emisă: Luna curentă"
    state = FilterState(
        uploaded=DateSpan(CUSTOM, dt.date(2026, 7, 8), dt.date(2026, 7, 18))
    )
    assert state.chips(_TODAY)[0].label == "Încărcată: 08.07.2026 – 18.07.2026"


def test_direction_chip_uses_the_romanian_pill_labels() -> None:
    state = FilterState(directions=frozenset({"sent", FAILING}))
    assert state.chips(_TODAY)[0].label == "Direcție: eșuată, trimisă"


def test_every_active_filter_gets_exactly_one_chip() -> None:
    state = FilterState(
        number="FCT",
        partner="ACME",
        from_cif="123",
        to_cif="456",
        directions=frozenset({"received"}),
        issued=DateSpan(mode=THIS_MONTH),
        uploaded=DateSpan(mode=THIS_MONTH),
        delayed_only=True,
    )
    keys = [chip.key for chip in state.chips(_TODAY)]
    assert len(keys) == len(set(keys)) == 8


# -- editing ------------------------------------------------------------------


def test_removing_one_chip_leaves_the_rest_alone() -> None:
    state = FilterState(partner="ACME", issued=DateSpan(mode=THIS_MONTH))
    trimmed = state.without(KEY_PARTNER)
    assert trimmed.partner == ""
    assert trimmed.issued.active is True


def test_every_chip_key_can_remove_itself() -> None:
    """A chip the bar can draw but not clear would be a dead ×."""
    state = FilterState(
        number="FCT",
        partner="ACME",
        from_cif="123",
        to_cif="456",
        directions=frozenset({"received"}),
        issued=DateSpan(mode=THIS_MONTH),
        uploaded=DateSpan(mode=THIS_MONTH),
        delayed_only=True,
    )
    for chip in state.chips(_TODAY):
        remaining = {c.key for c in state.without(chip.key).chips(_TODAY)}
        assert chip.key not in remaining


def test_unknown_filter_key_is_refused() -> None:
    with pytest.raises(KeyError, match="nope"):
        FilterState().without("nope")


def test_clearing_keeps_the_search_because_its_field_is_visible() -> None:
    state = FilterState(search="ACME", partner="X", delayed_only=True)
    cleared = state.cleared()
    assert cleared.search == "ACME"
    assert cleared.partner == ""
    assert cleared.delayed_only is False
    assert cleared.directions == ALL_DIRECTIONS


def test_removing_the_delayed_chip_keeps_any_upload_range() -> None:
    state = FilterState(uploaded=DateSpan(mode=THIS_MONTH), delayed_only=True)
    trimmed = state.without(KEY_DELAYED)
    assert trimmed.delayed_only is False
    assert trimmed.uploaded.active is True
    # ...and removing the range keeps the flag.
    assert state.without("uploaded").delayed_only is True


def test_direction_and_issued_keys_reset_to_their_unfiltered_value() -> None:
    state = FilterState(
        directions=frozenset({"sent"}), issued=DateSpan(mode=THIS_MONTH)
    )
    assert state.without(KEY_DIRECTIONS).directions == ALL_DIRECTIONS
    assert state.without(KEY_ISSUED).issued.active is False
