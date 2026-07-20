"""Live template preview — renders the user's path template as they type it.

Correctness comes from *reusing* production code: the real :class:`PathTemplate`
renders a fixed sample invoice context, and its :class:`TemplateError` is mapped
to the handoff's exact Romanian message. Nothing about rendering or validation
is reimplemented here, so the preview can never disagree with a real sync.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re

from ..template import PathTemplate, TemplateError

__all__ = ["PreviewResult", "render_preview", "sample_context"]

_UNKNOWN_VAR = re.compile(r"unknown template variable \{([^}]+)\}")


@dataclasses.dataclass(frozen=True)
class PreviewResult:
    """The preview outcome: a rendered path (ok) or a Romanian error (not ok)."""

    ok: bool
    text: str


def sample_context() -> dict[str, object]:
    """The handoff's sample invoice, covering every path-template variable.

    Values match §3: FCT-1001 / ACME CONSTRUCT S.R.L. / issued 2026-07-03 /
    cif 12345678 / received. Keys mirror :func:`anaf_sync.context.build_context`.
    """
    issue = dt.date(2026, 7, 3)
    created = dt.datetime(2026, 7, 6, 9, 30)
    return {
        "message_id": "3210447815",
        "request_id": "4similarid",
        "message_type": "FACTURA PRIMITA",
        "created": created,
        "created_month": "iulie",
        "cif": "12345678",
        "direction": "received",
        "number": "FCT-1001",
        "issue_date": issue,
        "issue_month": "iulie",
        "due_date": dt.date(2026, 8, 2),
        "currency": "RON",
        "total": 2480.00,
        "kind": "invoice",
        "seller_name": "ACME CONSTRUCT S.R.L.",
        "seller_cif": "12345670",
        "buyer_name": "STUDIO EXEMPLU S.R.L.",
        "buyer_cif": "12345678",
        "partner_name": "ACME CONSTRUCT S.R.L.",
        "partner_cif": "12345670",
    }


def render_preview(template: str, *, directory: str = "~/Facturi") -> PreviewResult:
    """Render ``template`` against the sample; green path or Romanian error.

    On success the text is the full archive path (rooted at ``directory``, with
    a ``.zip`` extension, as the sample invoice would land). An unknown variable
    yields the exact ``Variabilă necunoscută: {name}`` copy from the handoff.
    """
    try:
        rendered = PathTemplate(template).render(sample_context())
    except TemplateError as exc:
        return PreviewResult(False, _error_text(exc))
    return PreviewResult(True, f"{directory}/{rendered}.zip")


def _error_text(exc: TemplateError) -> str:
    match = _UNKNOWN_VAR.search(str(exc))
    if match is not None:
        return f"Variabilă necunoscută: {{{match.group(1)}}}"
    return "Șablon invalid"
