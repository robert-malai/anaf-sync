"""Path templating over invoice context variables.

The template language is Python's ``str.format`` mini-language: ``{number}``,
``{issue_date:%Y-%m}``, etc. Literal ``/`` in the template creates directories;
every *substituted* value is sanitised so a rogue invoice number can never
escape the output root or produce a name Windows refuses.

Beyond Python's standard ``!s``/``!r``/``!a`` conversions, four case
conversions are available on any variable: ``{issue_month!u}`` → ``IULIE``,
``{issue_month!l}`` → ``iulie``, ``{issue_month!c}`` → ``Iulie``, and
``{partner_name!t}`` → ``Furnizor Srl`` (per-word Title Case).
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

__all__ = ["PathTemplate", "TemplateError"]

#: Characters invalid on Windows (superset of POSIX) plus control characters.
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: Windows also rejects trailing dots/spaces on any path segment.
_TRAILING_JUNK = re.compile(r"[. ]+$")

_PLACEHOLDER = "unknown"

#: ``{var!x}`` case conversions; applied before sanitisation, ``None``-safe.
_CASE_CONVERSIONS: dict[str, Any] = {
    "u": str.upper,
    "l": str.lower,
    "c": str.capitalize,
    "t": str.title,
}


class TemplateError(ValueError):
    """The template references an unknown variable or renders an unusable path."""


def _sanitize_value(value: str) -> str:
    cleaned = _ILLEGAL_CHARS.sub("-", value).strip()
    cleaned = _TRAILING_JUNK.sub("", cleaned)
    return cleaned or _PLACEHOLDER


class _ContextFormatter(string.Formatter):
    """Formatter that only reads named keys and sanitises every substitution."""

    def __init__(self, context: Mapping[str, Any]) -> None:
        self._context = context

    def get_value(self, key: int | str, args: Any, kwargs: Any) -> Any:  # noqa: ANN401
        if isinstance(key, int):
            raise TemplateError(
                "positional fields like {0} are not supported — use named "
                "variables such as {number}"
            )
        if key not in self._context:
            available = ", ".join(sorted(self._context))
            raise TemplateError(
                f"unknown template variable {{{key}}} — available: {available}"
            )
        return self._context[key]

    def convert_field(self, value: Any, conversion: str | None) -> Any:  # noqa: ANN401
        if conversion in (None, "s", "r", "a"):
            return super().convert_field(value, conversion)
        if convert := _CASE_CONVERSIONS.get(conversion):
            # None stays None so format_field still renders the placeholder.
            return convert(str(value)) if value is not None else None
        raise TemplateError(
            f"unknown conversion {{!{conversion}}} — available: !u (CAPS), "
            "!l (small), !c (Capitalised), !t (Title Case), plus Python's "
            "!s/!r/!a"
        )

    def format_field(self, value: Any, format_spec: str) -> str:  # noqa: ANN401
        if value is None:
            return _PLACEHOLDER
        return _sanitize_value(format(value, format_spec))


class PathTemplate:
    """A validated path template rendered against an invoice context.

    The rendered result is a *relative* path: absolute templates and ``..``
    segments are rejected so output always stays under the configured root.
    """

    def __init__(self, template: str) -> None:
        template = template.strip().strip("/")
        if not template:
            raise TemplateError("the path template is empty")
        self._template = template

    def render(self, context: Mapping[str, Any]) -> PurePosixPath:
        """Render to a relative path (POSIX-style; ``pathlib`` adapts per OS).

        Raises:
            TemplateError: unknown variable, bad format spec, or a rendered
                path that is absolute / escapes upward.
        """
        try:
            rendered = _ContextFormatter(context).vformat(self._template, (), {})
        except (KeyError, IndexError, ValueError) as exc:
            if isinstance(exc, TemplateError):
                raise
            raise TemplateError(f"invalid template {self._template!r}: {exc}") from exc
        path = PurePosixPath(rendered)
        if path.is_absolute():
            raise TemplateError(f"template renders an absolute path: {rendered!r}")
        parts = [part for part in path.parts if part not in ("", ".")]
        if ".." in parts:
            raise TemplateError(f"template escapes the output root: {rendered!r}")
        if not parts:
            raise TemplateError(f"template renders an empty path: {rendered!r}")
        return PurePosixPath(*parts)

    def __repr__(self) -> str:
        return f"PathTemplate({self._template!r})"
