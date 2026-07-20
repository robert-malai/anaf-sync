# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`anaf-sync` is a cross-platform (Windows/Linux/macOS) CLI that archives RO
e-Factura invoices locally on a schedule. It is a thin, deliberate layer over
[anafpy](https://github.com/robert-malai/anafpy) — Robert's own package, which
also powers his local anafpy MCP server. Design rationale lives in
[DESIGN.md](DESIGN.md); read it before changing architecture-level behaviour.

## Commands

```bash
uv sync                                  # install deps (incl. dev group)
uv run pytest -q                         # tests
uv run ruff check src tests             # lint
uv run black --check src tests          # format check (black writes; ruff checks)
uv run mypy src                          # strict typing — must stay clean
uv run anaf-sync --help                  # run the CLI from the venv
```

All four gates (pytest, ruff, black, mypy --strict) must pass before a change
is considered done.

## Architecture map

| Module | Responsibility |
|---|---|
| `cli.py` | cyclopts commands; the only place exceptions are caught for the user |
| `config.py` | TOML sync config + `ANAFPY_*` env auth settings; `init` template |
| `engine.py` | one sync pass: list → dedupe → download (retry) → write artifacts |
| `context.py` | assembles the template variable dict for one message |
| `template.py` | `str.format`-based path template, sanitised per substitution |
| `logsink.py` | console/system log-mode detection + native sinks: Event Log, os_log, journald |
| `state.py` | JSON record of archived message ids (idempotence) + failure traces (visibility only, never a retry gate) |
| `scheduling.py` | registers `anaf-sync sync` with schtasks / systemd user / launchd |

## Invariants — do not break

- **Auth is anafpy's, not ours.** Credentials come from `ANAFPY_CLIENT_ID` /
  `ANAFPY_CLIENT_SECRET` and the token store written by `anafpy auth login`
  (`ANAFPY_TOKEN_STORE`, `ANAFPY_TOKEN_STORE_BACKEND`). Never introduce
  anaf-sync-specific credential storage or config keys.
- **Idempotence.** `state.json` is saved atomically after *every* archived
  message. A crash mid-run must never lose or duplicate work.
- **Path safety.** Every substituted template value is sanitised
  (`template.py`); rendered paths must stay relative and inside the output
  root. Windows-illegal characters and trailing dots/spaces are handled there
  — keep any new path logic behind that choke point.
- **Error philosophy** (mirrors anafpy): business outcomes are values,
  exceptions propagate; catch only at boundaries. In the engine, a per-message
  `AnafError` is recorded in the report and the run continues; everything else
  crashes the run on purpose. The CLI is the only layer that formats errors
  for humans and sets exit codes.
- **Cross-platform.** Anything touching paths, schedulers, or consoles must
  work on Windows, Linux, and macOS. No POSIX-only assumptions outside the
  platform-dispatched branches of `scheduling.py`.

## Sharp edges

- ANAF retains messages for **60 days** and rejects older windows; the
  1–60 bound on `lookback_days`/`--days` is ANAF's rule, not ours.
- The message listing never carries party CIFs as JSON fields; anafpy extracts
  them from the `detalii` prose. `context.py` treats them as best-effort.
- `DownloadedMessage.view` is `None` for non-UBL content (nok error files,
  buyer messages) *and* for rule-drift — the template must render regardless
  (missing values become `unknown`).
- anafpy's API is best learned from the installed source under
  `.venv/lib/python3.12/site-packages/anafpy/` — its docstrings are the spec.

## Conventions

Robert's standard Python stack applies (see the `python-conventions` skill):
Python 3.12+, `uv`, src layout, full type hints with `mypy --strict`,
Pydantic v2 for anything structured, `pydantic-settings` for env config,
`structlog` key-value logging, `httpx`/`tenacity` (via anafpy), `cyclopts` CLI
(matching anafpy),
`pytest` with pragmatic coverage. Google-style docstrings on public surfaces.

Tests use fakes at the `EFacturaClient` seam (`tests/test_engine.py`) and
`model_construct` to build invoice views without full UBL validation — follow
those patterns rather than mocking HTTP.
