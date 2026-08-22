# Contributing

A note on language: the user-facing documentation ([README.md](README.md)) is
in Romanian, because RO e-Factura only exists for entities with Romanian
fiscal obligations — the end-user audience is Romanian by construction.
Everything developer-facing — this file, [DESIGN.md](DESIGN.md),
[CLAUDE.md](CLAUDE.md), code, docstrings, commits, and issues — is in English.

## Setup

```bash
uv sync --extra tray --group qt    # installs deps, dev group, and the tray/Qt stack
uv run anaf-sync --help            # run the CLI from the venv
```

The `--extra tray --group qt` part matters: it is what CI runs. A plain
`uv sync` still works for core-only changes, but every `tests/test_tray_*.py`
file then skips via `importorskip` and mypy checks the tray modules with
PySide6 absent — so "all green" locally can still fail CI.

## Quality gates

All four must pass before a change is considered done:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -q    # offscreen lets the Qt suite run headless
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

## Cutting a release

The release commit carries both changes, then the tag:

1. Bump `version` in [pyproject.toml](pyproject.toml).
2. **Write `release-notes/<tag>.md`** (e.g. `release-notes/v0.3.0.md`) — every
   tag has one; [release-notes/](release-notes) holds all of them, backfilled
   to v0.1.0.
3. Commit as `Release X.Y.Z`, then push the `v*` tag.

**Release notes are written, not generated.** The file's first line is an H1
that becomes the GitHub release title (`# anaf-sync 0.3.0 — <the hook>`); the
rest is the body, prose in the voice of the existing files — what changed and
why it matters to a user, plus an explicit warning when something ships
unverified (the unsigned bundles are the standing example). Never hand-write
the compare link; `release.yml` derives and appends it. A tag with no such file
still gets a release, carrying GitHub's generated commit list — the fallback,
not the intent.

The `v*` tag is what does the rest: [release.yml](.github/workflows/release.yml)
re-runs the gates, checks the tag against `pyproject.toml`'s version, publishes
to PyPI via trusted publishing (OIDC, no stored token), and only then creates
the GitHub release with the sdist, the wheel, and the desktop packages
attached. PyPI first, deliberately — the release is the announcement, so it
must never point at a version `pip install` cannot reach yet. The bundles come
from [release-tray.yml](.github/workflows/release-tray.yml), which `release.yml`
calls; run it on its own via **workflow_dispatch** to check that the PyInstaller
freeze still works without cutting a release.

PyPI has no notes field: the `Changelog` project URL points every version's
project page at the releases, so the prose keeps one home.
