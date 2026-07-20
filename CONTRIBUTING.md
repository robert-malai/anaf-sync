# Contributing

A note on language: the user-facing documentation ([README.md](README.md)) is
in Romanian, because RO e-Factura only exists for entities with Romanian
fiscal obligations — the end-user audience is Romanian by construction.
Everything developer-facing — this file, [DESIGN.md](DESIGN.md),
[CLAUDE.md](CLAUDE.md), code, docstrings, commits, and issues — is in English.

## Setup

```bash
uv sync                    # installs deps, including the dev group
uv run anaf-sync --help    # run the CLI from the venv
```

## Quality gates

All four must pass before a change is considered done:

```bash
uv run pytest -q
uv run ruff check src tests
uv run black --check src tests    # black writes; ruff checks
uv run mypy src                   # strict typing — must stay clean
```

## Orientation

- [DESIGN.md](DESIGN.md) — why the tool is shaped the way it is. Read it
  before changing architecture-level behaviour.
- [CLAUDE.md](CLAUDE.md) — the architecture map, working conventions, and the
  invariants that must not break: auth belongs to anafpy (never introduce
  anaf-sync-specific credentials), idempotence via one committed transaction
  per archived message, all path logic behind the `template.py` choke point,
  errors caught only at the CLI
  boundary, and everything cross-platform (Windows/Linux/macOS).

anafpy's API is best learned from its installed source under
`.venv/lib/python3.*/site-packages/anafpy/` — its docstrings are the spec.

## Tests

Tests use fakes at the `EFacturaClient` seam (see
[tests/test_engine.py](tests/test_engine.py)) and `model_construct` to build
invoice views without full UBL validation. Follow those patterns rather than
mocking HTTP.

### Live tests

[tests/test_sync_live.py](tests/test_sync_live.py) exercises the real ANAF
**production** endpoints, strictly read-only — anaf-sync never files anything,
so unlike anafpy's roundtrip suites there is no TEST-environment upload here.
Archives and state land in pytest tmp dirs; your real archive and `state.db`
are never touched.

They need a repo-root `.env` (gitignored) with `ANAFPY_CLIENT_ID`,
`ANAFPY_CLIENT_SECRET`, and `ANAFPY_CIF`, plus a token store from
`anafpy auth login` (set `ANAFPY_TOKEN_STORE_BACKEND=file` if the login lives
in a file). Missing pieces skip, not fail. Run explicitly:

```bash
ANAFPY_LIVE=1 uv run pytest -q -m live
```
