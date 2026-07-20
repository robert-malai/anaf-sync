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
