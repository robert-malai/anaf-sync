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
| `engine.py` | one sync pass: list → dedupe → download (retry) → write artifacts → `repair_pdfs` (re-render missing PDFs from stored ZIPs; also `anaf-sync render`) |
| `backfill.py` | catalogs invoices already on disk (past ANAF's 60-day window, or a lost DB); read-only, and its rows never gate a download |
| `reprocess.py` | re-derives a synced row's catalog columns from its stored ZIP/XML (`anaf-sync reprocess`), and with `--move` re-renders the path template and relocates the files — the way back from an `unknown` projection; never re-derives what only ANAF's listing carried |
| `context.py` | assembles the template variable dict for one message |
| `template.py` | `str.format`-based path template, sanitised per substitution |
| `logsink.py` | console/system log-mode detection + native sinks: Event Log, os_log, journald |
| `state.py` | SQLite `Archive`: dedupe gate + permanent catalog of archived messages (idempotence) + pruned failure traces (visibility only, never a retry gate) + the `meta.last_run` `RunRecord` the tray/health read |
| `lock.py` | `filelock`-based `sync_lock` — one sync at a time; the DB cannot serialize runs |
| `health.py` | pure ok/warn/err derivation, purge countdown, delay rule — shared by `status` and the tray |
| `scheduling.py` | registers `anaf-sync sync` with schtasks / systemd user / launchd; also home of the shared script-resolution/subprocess helpers |
| `autostart.py` | login-time autostart for the tray (`anaf-sync tray install\|remove\|status`) |
| `tray/` | the desktop companion (PySide6, `tray` extra, `anaf-sync-tray` entry point): tray icon/menu (`app`), Facturi window (`window`, `models`, `delegates`, `details`), Setări (`settings_window`, `settings_view`, `template_help`, `preview`, `config_io`), plus `status`/`theme`/`icons`/`format` (pure) and `watcher`/`runner`/`store` (Qt edges) and `macos` (the accessory activation policy — no Dock icon without a bundle). `runner.CliRunner` spawns every subcommand the tray offers — `sync`, and `reprocess --move --message-id` behind the details pane's per-invoice button — under one in-flight guard |

## Invariants — do not break

- **Auth is anafpy's, not ours.** Credentials come from `ANAFPY_CLIENT_ID` /
  `ANAFPY_CLIENT_SECRET` and the token store written by `anafpy auth login`
  (`ANAFPY_TOKEN_STORE`, `ANAFPY_TOKEN_STORE_BACKEND`). Never introduce
  anaf-sync-specific credential storage or config keys.
- **Idempotence.** The archive DB commits one transaction per archived
  message (WAL, `synchronous=NORMAL`). A crash mid-run must never lose or
  duplicate work; downloaded records are permanent, so the dedupe gate is
  "was this message id *ever* archived".
- **The dedupe gate answers only for real ANAF message ids.** `backfill` rows
  carry a synthetic `backfill:<digest>` id and must never suppress a download:
  nothing on disk holds the `id_descarcare`, and the ZIP member names hold the
  *upload* index, which the sender's and receiver's copies of one invoice
  share. A duplicate costs one file; a false skip loses an invoice for good
  once ANAF's 60-day window shuts. `CatalogEntry.source` marks provenance —
  and warns readers that `created_at` is `None` on those rows, which
  `health.is_delayed` cannot distinguish from "on time".
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
  code path that mutates the archive. One deliberate exception to the
  ephemeral-connection pattern: the watcher's `_DataVersionProbe` holds the
  tray's single persistent connection (also `mode=ro`), reading only
  `PRAGMA data_version` to decide when a refresh is warranted — filesystem
  events alone cannot, because the tray's own reads touch `state.db-shm`.

## Sharp edges

- ANAF retains messages for **60 days** and rejects older windows; the
  1–60 bound on `lookback_days`/`--days` is ANAF's rule, not ours.
- Listing and downloading anchor those 60 days differently, so ANAF **lists
  messages it then refuses to download** (`AnafDownloadExpiredError`) — routine
  at the edge of a full-window lookback, i.e. on a first sync. The engine
  counts it (`SyncReport.expired`, `RunRecord.expired`) and records nothing;
  it is not a failure and must never become one. See DESIGN.md.
- The message listing never carries party CIFs as JSON fields; anafpy extracts
  them from the `detalii` prose. `context.py` treats them as best-effort.
- `DownloadedMessage.view` is `None` for non-UBL content (nok error files,
  buyer messages) *and* for rule-drift — the template must render regardless
  (missing values become `unknown`).
- **`QProcess` reports a failed launch synchronously on Windows** — the
  `CreateProcess` call fails inside `start()` — and asynchronously everywhere
  else, where the `exec` failure reaches the event loop a turn later. So
  `CliRunner.start` emits `started` *before* `process.start()`: emitting after
  delivered the pair inverted on Windows only, and the tray's handlers then ran
  in the order that leaves the menu disabled for a child that never ran. Tests
  around the in-flight guard fake at the `QProcess` seam for the same reason —
  aiming one at a missing executable encodes a single platform's timing as the
  contract.
- anafpy's API is best learned from the installed source under
  `.venv/lib/python3.12/site-packages/anafpy/` — its docstrings are the spec.

## Releases

A `v*` tag drives everything: `release.yml` re-runs the gates, checks the tag
against `pyproject.toml`'s version, publishes to PyPI via trusted publishing
(OIDC, no stored token), and only then creates the GitHub release with the
sdist, the wheel, and the three tray bundles attached. PyPI first, deliberately
— the release is the announcement, so it must never point at a version
`pip install` cannot reach yet. `release-tray.yml` is a **reusable** workflow
(`workflow_call` + `workflow_dispatch`) that only builds and uploads bundles;
it must never create or edit a release, or two jobs race for the same one.

Cutting a release — the release commit carries both, then the tag:

1. Bump `version` in `pyproject.toml`.
2. **Write `release-notes/<tag>.md`** (e.g. `release-notes/v0.3.0.md`) — every
   tag has one; `release-notes/` holds all of them, backfilled to v0.1.0.
3. Commit as `Release X.Y.Z`, then push the `v*` tag.

**Release notes are written, not generated.** The file's first line is an H1
that becomes the GitHub release title (`# anaf-sync 0.3.0 — <the hook>`); the
rest is the body, prose in the voice of the existing files — what changed and
why it matters to a user, plus an explicit warning when something ships
unverified. Never hand-write the compare link; `release.yml` derives and
appends it. A tag with no such file still gets a release, carrying GitHub's
generated commit list — the fallback, not the intent. PyPI has no notes field:
the `Changelog` project URL points every version's project page at the
releases, so the prose keeps one home.

Release notes are the one place the English-only rule bends: they are the
operator-facing announcement, so a Romanian lead line is fine when the release
is one operators act on (see `release-notes/v0.2.1.md`).

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
