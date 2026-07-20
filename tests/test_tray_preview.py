"""Template live preview via the production PathTemplate (no Qt)."""

from anaf_sync.config import _DEFAULT_TEMPLATE
from anaf_sync.tray.preview import render_preview, sample_context


def test_default_template_renders_the_sample_path() -> None:
    result = render_preview(_DEFAULT_TEMPLATE)
    assert result.ok
    # The real sanitiser strips the trailing dot of "S.R.L." (the mockup, a
    # static HTML, did not) — the preview mirrors what a sync would write.
    assert result.text == (
        "~/Facturi/12345678/received/2026/07/2026-07-03_FCT-1001_"
        "ACME CONSTRUCT S.R.L.zip"
    )


def test_unknown_variable_gives_exact_romanian_error() -> None:
    result = render_preview("{numer}")
    assert not result.ok
    assert result.text == "Variabilă necunoscută: {numer}"


def test_directory_override_roots_the_path() -> None:
    result = render_preview("{cif}", directory="/data/Facturi")
    assert result.ok
    assert result.text == "/data/Facturi/12345678.zip"


def test_sample_context_covers_every_documented_variable() -> None:
    # The handoff's valid-variable list (§3) must all resolve, not error.
    variables = [
        "number",
        "issue_date",
        "due_date",
        "currency",
        "total",
        "kind",
        "direction",
        "cif",
        "seller_name",
        "seller_cif",
        "buyer_name",
        "buyer_cif",
        "partner_name",
        "partner_cif",
        "message_id",
        "request_id",
        "message_type",
        "created",
    ]
    context = sample_context()
    for name in variables:
        assert name in context, name
        assert render_preview(f"{{{name}}}").ok, name
