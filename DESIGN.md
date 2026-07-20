# anaf-sync — design

Why this tool is shaped the way it is. Companion to [README.md](README.md)
(usage) and [CLAUDE.md](CLAUDE.md) (working conventions).

## 1. Problem and goals

ANAF's SPV purges e-Factura messages roughly **60 days** after filing. Any
business that wants a durable local archive must poll within that window and
keep its own copy. The goals, in order:

1. **Never lose an invoice.** Anything filed must land on disk before ANAF
   purges it, even across crashes, reboots, and flaky networks.
2. **Zero-attention operation.** Install once, schedule, forget. A run that
   finds nothing new is silent and cheap.
3. **Human-shaped archive.** The on-disk layout is the user's to define, from
   invoice data (`2026/07/2026-07-03_FCT-1001_ACME SRL.xml`), not ANAF's
   opaque message ids.
4. **Windows and Linux first-class** (macOS comes along for free — it is the
   development machine).

Non-goals: uploading/filing invoices, a GUI, multi-tenant server operation,
long-term document management (search, OCR, bookkeeping integration). The
archive is plain files; downstream tools take it from there.

## 2. Position in the anafpy ecosystem

anaf-sync is a *consumer* of anafpy, not a fork of its concerns. The split:

- **anafpy** owns everything ANAF-shaped: OAuth + token refresh, transport,
  pagination, response parsing, UBL models, the 60-day window rules.
- **anaf-sync** owns everything archive-shaped: what to keep, where to put
  it, what has already been fetched, and when to run.

The most consequential decision follows from this: **anaf-sync has no
credential system of its own.** It reads the same `ANAFPY_CLIENT_ID` /
`ANAFPY_CLIENT_SECRET` env vars and the same token store
(`anafpy auth login`, keyring or file backend, selected by
`ANAFPY_TOKEN_STORE_BACKEND` / `ANAFPY_TOKEN_STORE`) as the anafpy CLI and
MCP server. One browser login with the ANAF certificate serves every tool;
`TokenProvider` re-reads the store on each use, so a refresh performed by any
process is picked up by the others. The scheduled job depends on refresh
working headlessly, which is why missing client credentials are a hard,
early error rather than a warning.

## 3. The sync model: stateless window, stateful archive

Each run lists the **full lookback window** (default the whole 60 days) and
dedupes against a local state file, rather than tracking a "last synced"
timestamp.

Rationale: a timestamp cursor is fragile in exactly the ways that lose
invoices — clock skew, a failed run advancing the cursor, ANAF's listing
being eventually-consistent at the window edge. Listing is cheap (paginated
JSON); downloads are the expensive part, and the state file already gates
those. With overlapping windows every run gets a fresh chance at anything
previously missed, and a message only leaves the retry pool by being archived.

Mechanics (`engine.py` + `state.py`):

- The listing is materialised first so a pagination error aborts before any
  download work.
- `state.json` maps message id → record (when, where, which artifacts). It is
  written **atomically after every archived message** (temp file + rename,
  same pattern as anafpy's `FileTokenStore`), so a crash mid-run redoes at
  most the in-flight message — and that redo is harmless because downloads
  are idempotent GETs.
- Failures are per-message: an `AnafError` on one download is recorded in the
  `SyncReport` and the run continues. The next scheduled run retries it
  naturally, because it is still absent from the state file. Anything outside
  the `AnafError` hierarchy is a bug and crashes the run loudly.
- Persistent failures also leave a trace in the state file (first/last attempt,
  count, last error) so `anaf-sync status` can surface a message that keeps
  failing before the 60-day window closes on it. These records are
  **observability only** — they must never gate a retry; the record is cleared
  the moment the message finally archives, and pruned once its last attempt
  ages past `state_retention_days`.
- Transient transport and rate-limit errors retry in-process with
  exponential-jitter backoff (tenacity, 4 attempts) before counting as a
  failure. Only the idempotent download GET retries; mirroring anafpy's
  "single transparent call" stance, nothing non-idempotent ever does.
- Records older than `state_retention_days` (default 90) are pruned at the
  start of each non-dry run: past ANAF's 60-day retention a message id can
  never be listed again, so its record gates nothing. The configured value is
  floored at 60 for exactly that reason — pruning younger records would
  re-download messages still in the window. This keeps `state.json` bounded
  by the window, not by the archive's lifetime.

`--redownload` bypasses the state gate (re-fetch everything, e.g. after
changing the template); `--dry-run` reports what would be fetched without
touching disk or state.

## 4. Path templating

The archive layout is a template over per-invoice variables, e.g.

```
{cif}/{direction}/{issue_date:%Y}/{issue_date:%m}/{issue_date:%Y-%m-%d}_{number}_{partner_name}
```

**Language choice: Python's `str.format` mini-language,** not Jinja2.
Format specs give the two things a path actually needs — variable
substitution and formatting (crucially `strftime` specs on real dates) — with
zero dependencies, a syntax users already know, and no logic (loops,
conditionals, filters) to escape-hatch into. If real conditional layout is
ever needed, that is a sign the variable set is wrong, not that the template
needs a `{% if %}`.

Safety properties (`template.py`), enforced at one choke point:

- Every **substituted value** is sanitised: Windows-illegal characters and
  control chars become `-`, trailing dots/spaces are stripped. Literal `/` in
  the template creates directories; a `/` inside a value cannot.
- The rendered path must be relative and contain no `..` — output can never
  escape the configured root, whatever an invoice number contains.
- `None` renders as `unknown` rather than failing: an invoice that ANAF
  accepted must be archivable even when our parser cannot project a field.
- Unknown variables fail fast with the full list of available names —
  template typos surface on the first run, not as mis-filed invoices.

The variable set (`context.py`) is assembled from two tiers: the message
listing (always present) and the parsed UBL view (best-effort). `partner_*`
is the deliberate star: "the other party" resolved by direction, so one
template serves both received and sent archives. Party CIFs prefer the
invoice's own VAT fields and fall back to what anafpy extracts from the
listing's `detalii` prose (ANAF never sends them as structured fields).

A base path collision with an *existing different* file gets a `_{message_id}`
suffix rather than clobbering — two invoices may legitimately render the same
name.

## 5. Artifacts

Per message, the user picks any of:

| artifact | content | why |
|---|---|---|
| `zip` | the raw `descarcare` ZIP | the legally meaningful, signed original — tier-1 truth, byte-preserved |
| `xml` | invoice UBL extracted from the ZIP | convenient for downstream parsing |
| `signature` | detached MF signature XML | verification without unzipping |
| `pdf` | ANAF's own rendering | human-readable copy, via the public no-auth `transformare` service (`validate=False` — the document already passed validation at filing) |
| `metadata` | JSON sidecar: listing entry + resolved context | machine-readable index without re-parsing UBL |

Default is `["zip", "pdf"]`: the archive keeps the authoritative bytes plus
the copy humans actually ask for. The XML stays available inside the ZIP (and
as an opt-in artifact) — and since the PDF is rendered *from* that XML by a
public no-auth service, it can be regenerated later; only the ZIP is
unrecoverable after ANAF's window. The PDF client is only constructed when the
artifact is enabled, and a non-PDF response (ANAF answers HTTP 200 with a
JSON error) is a logged skip, not a failure — the invoice itself is already
safe on disk.

## 6. Configuration split

Two layers, on purpose:

- **TOML file** (`config.toml`, platformdirs config dir) for behaviour: CIFs,
  direction, window, output template, artifacts. Human-owned,
  diffable, commented by `anaf-sync init`, readable with stdlib `tomllib`.
  There is deliberately no `environment` key: ANAF's TEST inbox only ever
  holds messages you uploaded there yourself, so an archiver pointed at it
  syncs nothing real, and every operation we perform is a read — production
  is always safe. `--dry-run` covers the "preview without writing" need,
  against the real inbox.
- **Environment variables** for secrets and machine wiring: the `ANAFPY_*`
  family (§2), plus `ANAF_SYNC_CONFIG` to relocate the config file. Secrets
  never live in the TOML.

State (`state.json`) lives in the platformdirs *state* dir, separate from
config: wiping or versioning configuration must not forget what has been
archived.

## 7. Scheduling: the OS's job, not ours

`anaf-sync schedule install` registers `anaf-sync sync` with the native
scheduler; there is no daemon, no long-running process, no internal cron:

- **Windows** — Task Scheduler via `schtasks` (interval → `/SC MINUTE|HOURLY|DAILY /MO n`, `--daily-at` → `/SC DAILY /ST`).
- **Linux** — systemd **user** units (`anaf-sync.timer` + `.service`,
  `Persistent=true` so missed runs fire on wake; `loginctl enable-linger`
  documented for logged-out operation).
- **macOS** — a launchd agent (`StartInterval` / `StartCalendarInterval`).

Rationale: native schedulers survive reboots, handle wake-from-sleep and
missed windows, and are inspectable with tools operators already know. The
CLI resolves its own console-script path at install time so the job works
without any venv activation. Because runs are idempotent (§3), overlapping
or missed schedules are harmless — the schedule needs to be *roughly* right,
never precise.

## 8. Error handling and observability

Mirrors anafpy's hybrid model:

- **Values for business outcomes**: the `SyncReport` (listed / new / already
  archived / non-invoice / failures) is the result of a run; per-message
  failures are data in it.
- **Exceptions for broken preconditions**: missing config, missing
  credentials, invalid template, unexpected response shapes. These propagate
  to the CLI boundary, which is the only place they are formatted for humans
  and turned into exit codes (non-zero when anything failed, so the OS
  scheduler's failure status is meaningful).
- `structlog` key-value logging throughout (`archived`,
  `message_id=…, path=…`); `--verbose` for debug.
- **Logs go where the platform's own tools look** (`logsink.py`). An
  interactive run (stderr is a TTY) keeps the pretty console renderer. A
  scheduled run logs through the OS's native facility directly — the Windows
  Application event log via `ReportEvent`, the macOS unified log via
  `os_log` (subsystem `ro.anaf-sync`), journald via its native datagram
  socket — so Event Viewer / `Get-WinEvent`, `log show`/`log stream`, and
  `journalctl` work with no capture files or pipes in between, and severity
  filtering maps onto each facility's own levels. `ANAF_SYNC_LOG=console|system`
  overrides the TTY detection. In system mode the CLI boundary also logs
  `run_failed` / `sync_done` events and installs an excepthook that records
  crash tracebacks (`run_crashed`), because a scheduled run's stderr goes
  nowhere.

## 9. Known trade-offs and future work

- **Sequential downloads.** Deliberate: ANAF enforces daily call quotas and
  rate limits, and a nightly batch is not latency-sensitive. Concurrency is
  the first knob to turn if volumes ever demand it.
- **State is a JSON file.** Right up to tens of thousands of messages; SQLite
  is the successor if listing volumes or query needs grow.
- **Purge awareness, not purge alerts.** A message that fails for 60 days
  straight ages out of ANAF's window and is lost. Failures are visible in
  every run's report and exit code; an explicit "about to age out" warning
  would be a cheap, worthwhile addition.
- **No archive verification command.** `anaf-sync verify` (re-hash artifacts
  against state, validate MF signatures via `validate_signature`) is a
  natural extension.
