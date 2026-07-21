"""Template live preview via the production PathTemplate (no Qt)."""

from anafpy.efactura import MessageListItem

from anaf_sync.config import _DEFAULT_TEMPLATE
from anaf_sync.context import build_context
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


def test_sample_context_mirrors_the_real_template_context() -> None:
    """The sample is the legend's data source, so it must not drift.

    `template_help` renders its reference table from `sample_context`, and the
    engine renders real paths from `build_context` — a key in one and not the
    other means the panel documents a variable that does not exist, or hides
    one that does.
    """
    real = build_context(
        MessageListItem.model_construct(
            id="3001",
            request_id="5001",
            message_type="FACTURA PRIMITA",
            created_at="202607181430",
        ),
        None,
        cif="12345678",
    )
    assert set(sample_context()) == set(real)


def test_every_sample_variable_renders() -> None:
    for name in sample_context():
        assert render_preview(f"{{{name}}}").ok, name
