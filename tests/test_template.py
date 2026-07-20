"""Path template rendering."""

import datetime as dt
from pathlib import PurePosixPath

import pytest

from anaf_sync.template import PathTemplate, TemplateError


def test_renders_variables_and_directories() -> None:
    template = PathTemplate("{cif}/{direction}/{number}")
    result = template.render({"cif": "123", "direction": "received", "number": "F-42"})
    assert result == PurePosixPath("123/received/F-42")


def test_date_format_specs() -> None:
    template = PathTemplate("{issue_date:%Y}/{issue_date:%m}/{issue_date:%Y-%m-%d}")
    result = template.render({"issue_date": dt.date(2026, 7, 3)})
    assert result == PurePosixPath("2026/07/2026-07-03")


def test_case_conversions() -> None:
    template = PathTemplate("{m!u}/{m!c}/{m!l}/{m}")
    result = template.render({"m": "iulie"})
    assert result == PurePosixPath("IULIE/Iulie/iulie/iulie")


def test_title_case_conversion() -> None:
    template = PathTemplate("{partner_name!t}")
    result = template.render({"partner_name": "FURNIZOR de PANIFICAȚIE srl"})
    assert result == PurePosixPath("Furnizor De Panificație Srl")


def test_case_conversion_of_none_renders_as_unknown() -> None:
    template = PathTemplate("{issue_month!u}")
    result = template.render({"issue_month": None})
    assert result == PurePosixPath("unknown")


def test_unknown_conversion_is_rejected() -> None:
    template = PathTemplate("{m!x}")
    with pytest.raises(TemplateError, match="unknown conversion"):
        template.render({"m": "iulie"})


def test_unknown_variable_lists_available() -> None:
    template = PathTemplate("{nope}")
    with pytest.raises(TemplateError, match="unknown template variable"):
        template.render({"number": "1"})


def test_values_are_sanitised_for_the_filesystem() -> None:
    template = PathTemplate("{number}_{partner_name}")
    result = template.render(
        {"number": "FCT/2026:01", "partner_name": 'ACME "SRL" <RO>'}
    )
    # Slashes in a *value* must not create directories.
    assert len(result.parts) == 1
    assert result.name == "FCT-2026-01_ACME -SRL- -RO-"


def test_windows_reserved_names_are_prefixed() -> None:
    template = PathTemplate("{name}")
    assert template.render({"name": "NUL"}) == PurePosixPath("_NUL")
    assert template.render({"name": "con.backup"}) == PurePosixPath("_con.backup")
    assert template.render({"name": "lpt1"}) == PurePosixPath("_lpt1")
    # Only exact stems are reserved — ordinary names pass untouched.
    assert template.render({"name": "console"}) == PurePosixPath("console")


def test_overlong_values_are_truncated() -> None:
    template = PathTemplate("{name}")
    result = template.render({"name": "x" * 300})
    assert len(result.name) == 120


def test_none_renders_as_unknown() -> None:
    template = PathTemplate("{cif}/{number}")
    result = template.render({"cif": "123", "number": None})
    assert result == PurePosixPath("123/unknown")


def test_escape_attempts_are_rejected() -> None:
    with pytest.raises(TemplateError, match="escapes"):
        PathTemplate("../{number}").render({"number": "x"})


def test_empty_template_is_rejected() -> None:
    with pytest.raises(TemplateError, match="empty"):
        PathTemplate("  /  ")
