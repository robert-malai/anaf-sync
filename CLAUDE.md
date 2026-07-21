# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`anaf-sync` is a cross-platform (Windows/Linux/macOS) CLI that archives RO
e-Factura invoices locally on a schedule, plus an optional desktop tray
companion (`anaf-sync[tray]`, PySide6) that observes the archive. It is a
thin, deliberate layer over
[anafpy](https://github.com/robert-malai/anafpy) — Robert's own package, which
also powers his local anafpy MCP server. Design rationale lives in
[DESIGN.md](DESIGN.md); read it before changing architecture-level behaviour.

## Commands

```bash
uv sync --extra tray --group qt          # install deps — what CI runs; plain
                                         #   `uv sync` silently skips all tray
                                         #   tests and un-types the Qt code
QT_QPA_PLATFORM=offscreen uv run pytest -q   # tests (offscreen for the Qt suite)
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
| `state.py` | SQLite `Archive`: dedupe gate + permanent catalog of archived messages (idempotence) + pruned failure traces (visibility only, never a retry gate) + the `meta.last_run` `RunRecord` the tray/health read |
| `lock.py` | `filelock`-based `sync_lock` — one sync at a time; the DB cannot serialize runs |
| `health.py` | pure ok/warn/err derivation, purge countdown, delay rule — shared by `status` and the tray |
| `scheduling.py` | registers `anaf-sync sync` with schtasks / systemd user / launchd; also home of the shared script-resolution/subprocess helpers |
| `autostart.py` | login-time autostart for the tray (`anaf-sync tray install\|remove\|status`) |
| `tray/` | the desktop companion (PySide6, `tray` extra, `anaf-sync-tray` entry point): tray icon/menu (`app`), Facturi window (`window`, `models`, `delegates`, `details`), Setări (`settings_window`, `settings_view`, `template_help`, `preview`, `config_io`), plus `status`/`theme`/`icons`/`format` (pure) and `watcher`/`runner`/`store` (Qt edges) |

## Invariants — do not break

- **Auth is anafpy's, not ours.** Credentials come from `ANAFPY_CLIENT_ID` /
  `ANAFPY_CLIENT_SECRET` and the token store written by `anafpy auth login`
  (`ANAFPY_TOKEN_STORE`, `ANAFPY_TOKEN_STORE_BACKEND`). Never introduce
  anaf-sync-specific credential storage or config keys.
- **Idempotence.** The archive DB commits one transaction per archived
  message (WAL, `synchronous=NORMAL`). A crash mid-run must never lose or
  duplicate work; downloaded records are permanent, so the dedupe gate is
  "was this message id *ever* archived".
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
  platform-dispatched branches of `scheduling.py`/`autostart.py`.
- **The tray is a read-only observer.** It reads the archive via
  `Archive.open_readonly`, edits only `config.toml` (tomlkit round-trip), and
  delegates every sync to the `anaf-sync sync` CLI. Never give it a second
  code path that mutates the archive.

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

**Documentation languages.** `README.md` is the end-user guide and is written
in **Romanian** (with proper diacritics) — the audience is Romanian by
construction, since RO e-Factura only serves Romanian fiscal entities. Keep it
purely operator-facing (install, ANAF/SPV credentials, config, run, schedule,
logs). Everything developer-facing — `CONTRIBUTING.md`, `DESIGN.md`, this
file, code, docstrings, commits, issues — stays in English. Template variable
names, env vars, and CLI flags are code identifiers: never translate them.
