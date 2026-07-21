# anaf-sync — design

Why this tool is shaped the way it is. Companion to [README.md](README.md)
(the end-user guide, in Romanian), [CONTRIBUTING.md](CONTRIBUTING.md)
(developer setup), and [CLAUDE.md](CLAUDE.md) (working conventions).

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
OCR, bookkeeping integration. The archive is plain files; downstream tools
take it from there. A local browse/search UI over the archive catalog is on
the roadmap (§9) — the SQLite store (§3) already records what it will need —
but nothing here queries or serves that catalog yet.

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
dedupes against a local archive database, rather than tracking a "last synced"
timestamp.

Rationale: a timestamp cursor is fragile in exactly the ways that lose
invoices — clock skew, a failed run advancing the cursor, ANAF's listing
being eventually-consistent at the window edge. Listing is cheap (paginated
JSON); downloads are the expensive part, and the archive already gates those.
With overlapping windows every run gets a fresh chance at anything previously
missed, and a message only leaves the retry pool by being archived.

**The store is SQLite** (`state.py`, `Archive`), stdlib `sqlite3`, no new
dependency. It replaced an atomic-JSON file for one reason beyond size: it is
also the **permanent catalog** the future UI (§9) browses — partner, date,
number, direction, total — which a window-bounded, pruned JSON file could
never be. `messages` is keyed by ANAF message id, with `base_path` a `UNIQUE`
column (the path registry, below) and best-effort catalog columns projected
from the UBL view. `failures` and a `meta` (`schema_version`, and the desktop
companion's `last_run` blob — §10) table round it out. On open, a fresh DB gets
the current schema; an existing one is migrated forward by small, additive
steps (v1 → v2 added the nullable `created_at` column via `ALTER TABLE`, so old
rows simply keep NULL — the archive is a permanent catalog, never rebuilt), and
an unrecognised `schema_version` raises `ValueError`. Migrations stay additive
by design: there is no migration *framework* and no destructive rewrite, only
column adds a permanent catalog can absorb in place. A corrupt DB raises
`sqlite3.DatabaseError`, which crashes the run by design; deleting the file is
safe recovery, costing at most a 60-day re-download.

Mechanics (`engine.py` + `state.py`):

- The listing is materialised first so a pagination error aborts before any
  download work.
- Each mutating method **commits one transaction before returning** — durability
  is the `Archive`'s contract, not the caller's — so a crash mid-run redoes at
  most the in-flight message, harmless because downloads are idempotent GETs.
  `journal_mode=WAL` with `synchronous=NORMAL` can lose at most the last commit
  on power loss (one harmless re-download next run) and lets the future UI read
  while a sync writes. Whole-run serialization is a separate concern, held by
  the `filelock`-based `sync_lock` (`lock.py`): the DB cannot serialize runs.
- **Downloaded records are permanent.** Past ANAF's 60-day retention a message
  id can never be listed again, so keeping its record forever can never cause
  a spurious skip — and the dedupe gate is simply "was this id *ever*
  archived". Permanence is what turns the store into a lifetime catalog; there
  is no pruning of `messages`.
- Failures are per-message: an `AnafError` on one download is recorded in the
  `SyncReport` and the run continues. The next scheduled run retries it
  naturally, because it is still absent from the archive. Anything outside the
  `AnafError` hierarchy is a bug and crashes the run loudly.
- Persistent failures also leave a trace in the `failures` table (first/last
  attempt, count, last error) so `anaf-sync status` can surface a message that
  keeps failing before the 60-day window closes on it. These records are
  **observability only** — they must never gate a retry; the record is cleared
  the moment the message finally archives, and pruned once its last attempt
  ages past `failure_retention_days`. Only failure traces are pruned, because
  only they go stale; the config key (default 90, `ge=1`, no floor) needs no
  60-day floor now that downloaded records are never at risk from it.
- Transient transport and rate-limit errors retry in-process with
  exponential-jitter backoff (tenacity, 4 attempts) before counting as a
  failure. Only the idempotent download GET retries; mirroring anafpy's
  "single transparent call" stance, nothing non-idempotent ever does.

`--redownload` bypasses the dedupe gate (re-fetch everything, e.g. after
changing the template); `--dry-run` reports what would be fetched without
touching disk or state — it opens the `Archive` without a retention argument,
so even failure-trace pruning is skipped.

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

Base path collisions are resolved by `Archive.claim_base` against the
`base_path` `UNIQUE` column, which doubles as the registry of which message
owns which path: a base recorded for a *different* message gets a
`_{message_id}` suffix (two invoices may legitimately render the same name),
while an unowned base — or this message's own prior path — is claimed and
overwritten in place. That policy lives in the store, not the engine, so the
engine holds no collision logic; and because downloaded records are permanent
(§3), the registry now spans the archive's whole lifetime. Deliberately,
`--redownload` refreshes files where they are and leftovers from a run that
crashed before recording are healed rather than duplicated.

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

The archive database (`state.db`) lives in the platformdirs *state* dir,
separate from config: wiping or versioning configuration must not forget what
has been archived — and now must not forget the catalog either.

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
- **Archive UI over the catalog.** The SQLite store (§3) already records the
  catalog tier (partner, date, number, direction, total, currency) the future
  browse/search UI needs; the schema is the commitment, but no read/query
  interface exists yet — it waits for the UI to define its needs rather than
  guessing them. Full-text search (SQLite **FTS5**) and a `reindex` command to
  backfill/rebuild catalog columns from the on-disk artifacts are the natural
  next steps there.
- **Purge awareness.** A message that fails for 60 days straight ages out of
  ANAF's window and is lost. Beyond the per-run report and exit code,
  `anaf-sync status` now prints an "expires from SPV in *N* days" countdown per
  failing message (`health.days_until_purge`), so an operator sees a persistent
  failure closing in before it is too late. The desktop companion (§10) surfaces
  the same signal as its amber/red states.
- **No archive verification command.** `anaf-sync verify` (re-hash artifacts
  against state, validate MF signatures via `validate_signature`) is a
  natural extension.

## 10. The desktop companion

A small system-tray application (`anaf_sync.tray`, an optional `tray` extra —
PySide6, GUI-free core stays intact) makes silent sync failures visible before
ANAF's 60-day purge, which a scheduled CLI job cannot do on its own. Its shape
follows directly from the invariants above:

- **Read-only observer.** The tray never downloads, uploads, deletes, or
  rewrites archive files. It reads the catalog through `Archive.open_readonly`
  (a `mode=ro` connection; WAL from §3 is what lets it query while a scheduled
  sync writes) and edits only `config.toml`, via a tomlkit round-trip that
  preserves the user's comments and formatting. Every actual sync is performed
  by spawning the same `anaf-sync sync` CLI — one code path for the schedule and
  the button alike, one `filelock` (§3) serialising both.
- **Three states, derived not stored** (`health.derive_health`, pure and
  tested). Any failure trace → **warn** (amber); a crashed last run, an
  auth/config-family failure, or a schedule that has gone silent past twice its
  interval → **err** (red); otherwise **ok** (green). `err` wins over `warn`.
  The inputs are the failure traces (§3) and the new **last-run record**
  (`RunRecord`, a JSON blob under `meta.last_run`) the CLI writes on every exit
  path — success, caught boundary error (with the exception's kind, so an
  expired token reads as red rather than amber), and the system-mode crash
  excepthook. Bookkeeping never masks the run: a failed `record_run` is logged
  and swallowed.
- **Schema v2 for the delay signal.** Flagging an invoice *declarată cu
  întârziere* needs both of its dates — the issue date (already stored) and when
  it entered SPV (ANAF's `data_creare`). The latter was parsed but dropped; v2
  persists it as `created_at` so `health.upload_delay_days` can compare them
  against a single `DELAY_THRESHOLD_DAYS` constant. The migration is the
  additive `ALTER TABLE` described in §3.

The companion is deliberately not a second way to *do* anything — it observes,
it configures, and it delegates every mutation to the CLI. That keeps the
archive's correctness properties (§3) entirely in one place.

**The layout is elastic; the design size is the minimum.** The window resizes
freely and every view follows its bounding box; 980×620 — the size the views
were designed at — is the *minimum*, not the size. All of it is expressed
through Qt layout stretch factors and size policies, never absolute geometry
or `resizeEvent` math, so one rule set holds at every size. The rules assign
each element one of two roles:

- *Anchored* (fixed on at least one axis): the title bar spans full width at
  fixed height; the sidebar keeps its fixed width and stretches vertically;
  the details pane keeps its fixed width against the right edge (it is a
  reading pane — widening it would only stretch line lengths); toolbar,
  period row, footer and save bar are full-width, fixed-height bands whose
  buttons/chips keep their natural size.
- *Stretching* (absorbs the slack): exactly one element per page takes both
  extra axes. On Facturi it is the catalog table — extra height shows more
  rows, extra width feeds the Partener column, the only non-fixed column (the
  rest are dates, sums, statuses of known width). Inside the toolbar the
  search field is likewise the one horizontal absorber. On Setări the scroll
  area takes the extra height (its scrollbar disappearing once the form
  fits); inputs stretch horizontally within the form, whose content column is
  capped at a comfortable reading width (~760px) and left-anchored beside the
  fixed 150px label column — a path-template field spanning a maximised 4K
  window helps nobody.

Window geometry persists across launches through `QSettings` (an `anaf-sync`
/ `tray` scope in the platform-native store — plist, registry, ini),
deliberately *not* `config.toml`: geometry is UI state, not sync
configuration, and a file the design promises to round-trip only on explicit
saves must not churn on every resize. The window is created lazily and hidden
on close, so within one tray session the size survives for free; across
launches it is Qt's blessed pair — `saveGeometry()` in `closeEvent` (and on
quit) and `restoreGeometry()` at construction — which also encodes maximised
state and pulls a remembered position back onto a screen that still exists
when monitors have detached. A missing or invalid blob falls back to the
980×620 design size, and the minimum size holds regardless of what was
stored. Tests point `QSettings` at a throwaway ini file so the suite never
touches the real per-user store.

**Config edits are round-trips, not rewrites.** The Setări form edits
`config.toml` through tomlkit: it mutates only the keys the user changed and
writes the document back atomically, so hand-written comments and layout
survive byte-for-byte. Every edit is validated against the real `SyncConfig`
*before* the write, so an invalid form leaves the file untouched, and the
template preview renders through the production `PathTemplate` (never a
reimplementation) so it can never disagree with what a sync would write.
Changing the schedule frequency re-installs through `scheduling.py`'s own
functions; the tray never shells out to `schtasks`/`systemctl`/`launchctl`
itself.

**The followed CUIs are an input, not a discovered set.** The Setări form takes
the CUI list as free entry: the user adds and removes entries, each validated
by the same rule as `config.py` (strip, upper-case, drop an `RO` prefix, must
be digits), with at least one surviving — `config.toml` is the source of truth
and the form is simply its editor. anafpy *does* expose an authorization
inventory (`SpvClient.list_messages(60).authorized_cuis`, surfaced as
`anafpy spv status` — it is the only endpoint that returns it), but it is
deliberately **not** wired in as the source of this list. It rides the SPV
certificate cookie session rather than the `ANAFPY_*` OAuth credentials the
rest of anaf-sync is built on (§2); that session expires within days, and
re-establishing it fires the certificate 2FA prompt — an interactive,
macOS/Windows-only choreography. ANAF also omits the identity fields entirely
when the queried window holds no messages, so the inventory can come back empty
for a perfectly valid session. A config editor that could not populate its own
company field without a PIN prompt would be a worse editor, so discovery stays
out of the write path. CUIs already seen in the archive are offered as
**autocomplete suggestions** on the entry field — a convenience over the
catalog, never a gate on what may be typed.

**Autostart is the platform's job too** (`autostart.py`, mirroring §7's stance
on scheduling): a macOS LaunchAgent (`RunAtLoad`, `ProcessType Interactive`, no
`KeepAlive` — a tray the user quits should stay quit), a Windows `HKCU\…\Run`
value, and an XDG `~/.config/autostart/*.desktop` entry, driven by
`anaf-sync tray install|remove|status`. The payload builders are pure functions
returning the plist dict / desktop text / registry string, so the format is
unit-tested without touching the real system; only install/remove/status make
the platform calls. The launched command is resolved exactly as `scheduling.py`
resolves `anaf-sync` — the console script, or `sys.executable` when frozen — so
autostart works from a venv install and from a bundle alike.

**Bundling** (`packaging/tray.spec`, one PyInstaller spec with platform
conditionals) freezes the app into a menu-bar-only macOS `.app` (`LSUIElement`,
so no Dock icon), a windowed Windows exe, and a Linux one-dir binary, excluding
the Qt modules the tray never touches to keep the size down. `release-tray.yml`
runs the full gates with the `tray` extra (the PySide6 code exercised headless
via `QT_QPA_PLATFORM=offscreen`) before building on each OS. Code signing and
notarization are deliberately out of scope for now — the bundles are unsigned
and trigger the usual first-run OS warnings, documented in the README with the
right-click-open workaround; signing is follow-up work before the bundles are
recommended for wide distribution.
