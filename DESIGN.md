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

Non-goals: uploading/filing invoices, a GUI for *creating or mutating*
anything, multi-tenant server operation, OCR, bookkeeping integration. The
archive is plain files; downstream tools take it from there. The desktop
companion (§10) does add a read-only browse UI over the archive catalog the
SQLite store (§3) records — but it observes and configures, never a second
way to change the archive.

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
- **An expired download is not a failure.** `listaMesaje` and `descarcare`
  anchor their 60 days differently, so ANAF lists messages it then refuses to
  hand over; any lookback reaching that far back meets the boundary band, which
  makes it routine on a first sync. anafpy names the condition
  (`AnafDownloadExpiredError`), and the engine logs it, counts it in
  `SyncReport.expired`, and lets it go: it writes no `failures` row, because
  that table is for things that might still succeed and an amber nobody can
  clear is noise; and no `messages` row, because nothing reached the disk. The
  count also lands in the `RunRecord`, which is where it earns its keep — on a
  *later* run, expired messages mean the schedule had a gap wide enough to lose
  invoices, and that is worth a glance at `anaf-sync status`. Nothing suppresses
  a re-attempt, so a spurious verdict costs one retry rather than the invoice,
  and the message drops out of the listing on its own within days.
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

"Can be regenerated later" is a mechanism, not just a consolation: the dedupe
gate never revisits an archived message, so a skipped render would otherwise be
permanent (ANAF's WAF is known to reject legitimate invoice XML — see
[anaf-sync#4](https://github.com/robert-malai/anaf-sync/issues/4)). The engine
therefore ends every sync pass with `repair_pdfs`: the catalog's `artifacts`
column is the worklist (`sync` rows without `"pdf"`), the stored ZIP is the
source, and a successful render appends to that column — the gate itself is
never consulted or changed. The same pass is exposed as `anaf-sync render` for
one-shot repairs. Its outcomes never fail a run: unlike a missed download,
nothing here is deadline-bound, and the next pass retries whatever is still
missing. Backfill rows are excluded — they catalog folders the engine does not
own, and writing new files into them is the operator's call.

The same argument generalises past the PDF, to everything *derived* from the
document rather than downloaded with it. When anafpy cannot read a document
ANAF accepted (§8's rule drift), the message still archives — but every
XML-derived value collapses to `unknown`: the catalog columns the tray shows,
and the rendered path itself. ANAF offers no way back, since the dedupe gate
never revisits a message and `--redownload` cannot reach past the 60-day
window. The stored ZIP can, forever, so `anaf-sync reprocess` re-runs the
projection off disk: `--refresh` semantics by default (rewrite the catalog
columns), and `--move` to re-render the path template and relocate the
message's files under it. Two tiers because they differ in blast radius, not
in confidence — a column rewrite is invisible from disk, while a move touches
the operator's own files (and by the same mechanism re-files the whole archive
after a template change, which is why it is opt-in and honours `--dry-run`).

What reprocessing must *not* invent is the other half of a message. The
listing entry it was first projected from is long gone, so `message_type` and
`created_at` (`data_creare`, which the delay flag reads) stay as the download
recorded them — re-deriving them as `None` would silently turn "unknown delay"
into "on time". `request_id` is the one path variable with no home in the
archive at all; a template referencing it refuses the move outright rather
than render `unknown` over paths that already hold the real value. The move is
planned before it is applied, tolerates a half-finished predecessor (a
destination whose source is gone is a move already made), refuses any
destination still occupied, and orders the file it re-read the message *from*
last, so an interrupted pass always resumes.

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

- **Windows** — Task Scheduler via `schtasks` (sub-day intervals →
  `/SC MINUTE /MO n`, whole days → `/SC DAILY /MO n`, `--daily-at` →
  `/SC DAILY /ST`; anything over a day that is not a whole number of days is
  rejected rather than rounded).
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
- **Catalog search depth.** The desktop companion's Facturi window (§10) is
  the browse UI over the catalog tier the SQLite store (§3) records: it pages
  through `Archive.open_readonly` with SQL-side filtering (`catalog` /
  `catalog_count`). Full-text search (SQLite **FTS5**) is the natural next step
  there; the other half of this bullet — rebuilding catalog columns from the
  on-disk artifacts — shipped as `anaf-sync reprocess` (§5).
- **Purge awareness.** A message that fails for 60 days straight ages out of
  ANAF's window and is lost. Beyond the per-run report and exit code,
  `anaf-sync status` now prints an "expires from SPV in *N* days" countdown per
  failing message (`health.days_until_purge`), so an operator sees a persistent
  failure closing in before it is too late. The desktop companion (§10) surfaces
  the same signal as its amber/red states.
- **No archive verification command.** `anaf-sync verify` (re-hash artifacts
  against state, validate MF signatures via `validate_signature`) is a
  natural extension.
- **The GitHub release is automated, its prose is not** (2026-07-26). A `v*`
  tag creates the release itself, downstream of the PyPI publish job — the
  release is the announcement, so it must never point at a version
  `pip install` cannot yet reach. The body comes from `release-notes/<tag>.md`
  committed with the release: through v0.2.3 those notes were written by hand
  after the fact (and four tags never got a release at all, because the only
  job creating one was the bundle upload, which has no notes to give), so the
  automation moves the *creation* into CI and leaves the *writing* where it
  was. GitHub's generated notes remain the fallback when a tag ships no such
  file, so a release always exists for a published version. The v0.1.0–v0.2.3
  notes were **backfilled into `release-notes/`** — from the published bodies
  where there was one, from the commit history where there was not — so the
  directory, not the GitHub API, is now the corpus. Consequence for the tray:
  `release-tray.yml` became a reusable workflow that only uploads artifacts,
  and `release.yml` attaches them; two workflows racing to create the same
  release is what produced v0.2.3's empty body. Notes are **not** duplicated
  into the packaged README for PyPI's sake (PyPI has no notes field, and the
  shipped README would then drift from the repo's): a `Changelog` project URL
  points every version's project page at the releases.

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
  the button alike, one `filelock` (§3) serialising both. Refreshes are driven
  by data, not file events: WAL readers write read-marks into `state.db-shm`,
  so the tray's own reads generate filesystem events, and refreshing on those
  fed a self-sustaining reset loop. The watcher instead treats events as a
  prompt to compare `PRAGMA data_version` over one persistent `mode=ro`
  connection — the counter moves only when another connection commits, with
  file *identity* checked separately so a deleted or rebuilt `state.db` still
  registers through the pinned inode. The cost of that persistent handle is
  paid on Windows, which refuses to unlink a file another handle has open:
  `state.db` cannot be deleted or replaced while the tray runs. Syncing is
  untouched (WAL admits the concurrent writer; only deletion hits the sharing
  violation), so the price is that rebuilding a lost archive there means
  quitting the tray first — cheaper than dropping the handle and reopening the
  refresh loop it closes.
- **Three states, derived not stored** (`health.derive_health`, pure and
  tested). Any failure trace → **warn** (amber); a crashed last run or an
  auth/config-family failure → **err** (red); otherwise **ok** (green). `err`
  wins over `warn`.
  The inputs are the failure traces (§3) and the new **last-run record**
  (`RunRecord`, a JSON blob under `meta.last_run`) the CLI writes on every exit
  path — success, caught boundary error (with the exception's kind, so an
  expired token reads as red rather than amber), and the system-mode crash
  excepthook. Bookkeeping never masks the run: a failed `record_run` is logged
  and swallowed.
- **Schema v2 for the delay signal.** Flagging an invoice *declarată cu
  întârziere* needs both of its dates — the issue date (already stored) and when
  it entered SPV (ANAF's `data_creare`). The latter was parsed but dropped; v2
  persists it as `created_at` so `health.upload_delay_working_days` can compare
  them against a single `DELAY_THRESHOLD_WORKING_DAYS` constant — *working*
  days (Mon–Fri), because that is how the e-Factura reporting deadline is
  written; public holidays are deliberately not modelled (a soft warning does
  not justify a legal-holiday calendar that must track law changes). The
  migration is the additive `ALTER TABLE` described in §3.

The companion is deliberately not a second way to *do* anything — it observes,
it configures, and it delegates every mutation to the CLI. That keeps the
archive's correctness properties (§3) entirely in one place. `CliRunner` is
that delegation: one child process at a time, whichever subcommand it is
(`sync` from the menu and the failing-message retry, `reprocess` from the
details pane's per-invoice button). The single guard is not tidiness — two
tray-spawned children would meet at the sync lock and the second would simply
die on it, so the honest UI is one that does not offer the second click.
The guard is raised before the child is launched, and `started` is emitted
before `QProcess.start()` rather than after: Windows fails a launch *inside*
that call, where every other platform reports it a turn later through the event
loop, so emitting afterwards delivered `finished` ahead of `started` on Windows
alone — and the tray, taking the two in the order they arrived, re-enabled its
menu and then disabled it forever, waiting on a process that never ran. Signal
order is the contract here; the platform's timing is not.

**Repairing one invoice from the pane.** A row whose number, issue date and
partner are *all* blank is the signature of the unreadable projection §5
describes, and the details pane says so — an amber panel that an operator
seeing an invoice with no data reasonably needs, because the fear it answers
("did the download fail?") is wrong: only the reading failed, and the signed
original is on disk. The repair button sits below the file buttons rather than
beside them (those two only open what is already there; this one re-reads the
invoice and may move it), is promoted to primary exactly where those blanks
appear, and stays available — quietly — everywhere else, which is how a single
invoice gets re-filed after a template change. It runs
`reprocess --move --message-id`: fixing the catalog but leaving the invoice in
the `unknown` folder would repair only half of what the operator can see, and
send them to a terminal for the rest. Backfill rows show it disabled with the
reason, since `Archive.synced` excludes them by construction and an enabled
button would promise something the CLI would refuse.

**Two windows, not one stack.** Facturi and Setări are separate top-level
windows rather than pages of a sidebar-switched stack. The split follows from
what each one is: the catalog is a surface the user leaves open and glances at,
while Setări is a bounded editing task with an explicit commit boundary —
*Salvează modificările* writes `config.toml`, *Renunță* (and Esc, and the
window's close button, all one reject slot) discards. Inside a single window
those two verbs had no honest target. Cancelling a form the user reached by
clicking a nav item either strands them on a reverted page or silently throws
them back to a list they never asked for; and a save bar pinned under a stack
implies the whole window is unsaved, which the catalog half never is. As
separate windows the answer is the one every desktop already teaches: the
editor closes and the thing behind it is still there. Nothing outside the
Setări window depends on its pending state, so closing with unsaved edits needs
no confirmation prompt — reopening re-reads `config.toml` and a cancelled
session leaves no residue. Setări opens from the tray's *Setări…* item and from
a toolbar button in Facturi, and carries its own geometry key and its own,
smaller size range — a 760×620 design minimum (the width floor is *derived*:
the window asks the form, whose narrowest measurable element is the variable
reference panel, so on wide-font platforms the minimum sits above 760 — #1)
up to a 1200×780 maximum, against the catalog's 1160×620 design size — whose
width floor is derived the same way, from the columns rather than the form — and no
maximum at all — because the two have opposite appetites for space, which the
next paragraphs make precise.

**The layout is elastic; the design size is the minimum.** Each window resizes
freely and follows its bounding box; the size it was designed at is the
*minimum*, not the size (Setări also has a maximum — below). All of it is expressed through Qt layout stretch
factors and size policies, never absolute geometry or `resizeEvent` math, so
one rule set holds at every size. The rules assign each element one of two
roles:

- *Anchored* (fixed on at least one axis): the details pane keeps its fixed
  width against the right edge (it is a reading pane — widening it would only
  stretch line lengths); toolbar, active-filter bar, footer and save bar are
  full-width, fixed-height bands whose contents keep their natural size. The
  filter bar's height is *zero* when nothing is filtered, which is why moving
  the filters into the header cost the default view no chrome at all.
- *Stretching* (absorbs the slack): exactly one element per window takes both
  extra axes. On Facturi it is the catalog table — extra height shows more
  rows, extra width feeds the Partener column, the only stretch section (the
  rest are dates, sums, statuses of known width). Inside the toolbar the
  search field is likewise the one horizontal absorber. On Setări the scroll
  area takes the extra height (its scrollbar disappearing once the form fits)
  and the field column takes the extra width beside the fixed 150px label
  column.

**The filters live on the columns they filter.** Facturi carried a row of
direction chips and a period row above the table; both are gone into the header,
where clicking a column label sorts by it and clicking its ▽ opens that column's
filter. The rearrangement is not cosmetic — it resolves three things the chips
could not. A chip row grows linearly with the number of filters, and the column
set had outgrown it; "Probleme" was one control doing two unrelated jobs, which
split cleanly once each half could sit on the column that carries its evidence
(*întârziate* on Încărcată, whose cell already turns amber; *eșuate* as a value
of the Direcție checklist); and search, the one filter spanning two columns
(`number` OR `partner_name`), is exactly the one with no column to move to — so
it keeps the toolbar to itself, and the toolbar keeps a reason to exist.

The cost of header filters is that an active one is invisible once its popover
closes, and a catalog silently missing rows is worse than one that does not
filter at all. The **active-filter bar** pays it: every active filter is echoed
under the search field as a removable label. `FilterState` — a frozen dataclass that touches no
widget — is the single value all three readers derive from — the model's query, the header's set
of lit funnels, the bar's chips — so they cannot disagree about whether a filter
is on.

**Facturi derives its width floor too.** Every fixed section sizes itself from
the platform's font — its label plus the two marks the header now paints — so the
columns are wider than the px the mockup was measured at, and by a different
amount on each desktop. A constant minimum would squeeze Partener, the column a
reader actually scans, hardest on exactly the machines whose metrics are widest.
The window instead floors its width at what the measured columns need plus 200px
of Partener plus the details pane, which is the same move Setări makes from its
variable reference panel — and the reason the px in the handoff are documented as
floors rather than targets.

**Sorting is SQL, not a proxy model.** `CatalogModel` pages through `fetchMore`,
so a `QSortFilterProxyModel` would order only the rows already fetched and
re-shuffle the list under the reader as they scroll. `Archive.catalog` takes an
`order_by` validated against a whitelist — the value is spliced into `ORDER BY`,
so nothing outside that table may reach it — and closes every ordering with
`message_id DESC`. That tiebreak is not tidiness: without a unique final key,
`LIMIT`/`OFFSET` paging over a non-unique sort column duplicates and skips rows
between pages. Blanks sort last in both directions, which is what the fixed
order always did with `issue_date IS NULL`. Direcție is deliberately absent from
the whitelist: three values make a filter, not an order.

**De la CIF / Pentru CIF are roles, derived per row.** An invoice goes *from*
one CIF *to* another, and which of them is the followed one depends on
`direction`: on a received invoice the partner issues and you receive, on a sent
one they swap. Two absolute columns beat "CIF" plus "CIF partener" because the
reader never has to hold the direction in their head and do the substitution —
and the followed CIF is painted at full strength against the counterparty's
muted, so which side of the flow you are on is legible without reading digits.
Neither is stored: both are `CASE direction WHEN 'sent' THEN … END` in SQL, with
`state.role_cifs` as the Python mirror for rendering and a test pinning the two
statements together. A failing message has neither — nothing was downloaded to
read a partner CIF from, and the `failures` table records no CIF of its own.

**The details pane collapses when it has nothing to say.** With no selection it
folds to a 30px rail and the table takes the width back, which is exactly when
the reader wants it: scanning. Selecting a row opens it; re-clicking that row
deselects and folds it again (Qt's single-selection mode offers no other way
out); the pane's own `›` pins it shut, and that pin is a *preference* — it
survives selections and restarts, because a fold that the next click undid would
make both the button and the saved state meaningless. One consequence reaches
the model: the selection now survives a filter change unless the row it names
was filtered out. Clearing it unconditionally, as the first cut did, throws away
a pane mid-read — and with an auto-collapsing pane it makes the whole right side
flap on every keystroke in the search field.

**Setări has a maximum size; Facturi does not.** The catalog is unbounded — more
width and height are always more invoices and longer partner names, so the only
ceiling is the screen. A configuration form is the opposite: it holds a fixed
amount of content, and past the point where all of it is visible without
scrolling, every additional pixel is empty space with a save bar stranded at the
bottom of it. So Setări is clamped (`setMaximumSize`) at 1200×780. The height is
derived, not chosen: 780 is where the form stops scrolling at its *narrowest*
allowed width, which makes the promise simple — at maximum height nothing
scrolls, whatever the width. The width is derived too, from the widest thing
the form has to lay out: five artifact cards on one row (below), which also
leaves a default-length path template and its rendered preview each on a single
line.

Within that range the fields genuinely use the space rather than sitting at a
fixed cap: the archive directory row, the template field and its preview each
span the full field column, and the artifact cards widen with it.

The cards also **re-flow on column count, between exactly two layouts**: 3-up
(two rows, 3 + 2) and 5-up (one row), switching when the field column can give
every card ~170px. Four columns are excluded on purpose — five cards in four
columns strand `metadata` alone on a second row, and 3 + 2 and 5 are the only
clean partitions of five. The 170px floor is why the switch is worth having at
all: it fires when the one-row layout is an *improvement*, not as soon as it is
geometrically possible (at 150px per card the descriptions wrap to three lines
and 5-up is worse than the 3-up it replaced). This is what sets the maximum
width: 1100 only just fits five cards, 1200 makes them legible at ~191px with
every description but `metadata` on one line. The grid always fills the field
column — a per-card maximum width would leave a ragged right edge mid-range,
breaking the alignment with the full-width fields directly above it. In Qt this
is a small `QLayout` subclass, not `resizeEvent` arithmetic, so it stays inside
the rule the elastic layout is built on.

Two controls deliberately opt out of stretching. The `lookback_days` slider caps at 480px: 60 steps stretched across 900px
is pixel-hunting, and a slider that long reads as a progress bar. Help text caps
at 620px because it is prose, and prose has a reading width no window size
changes. Radios, the frequency select and the directory-picker button keep their
natural size, as controls sized to their content should.

**The user re-proportions the table; the layout still holds.** The four
narrow columns are `Interactive` sections and Partener stays `Stretch`, so
dragging any header boundary moves that one boundary and Partener absorbs the
difference — the table can never be dragged wider than its viewport or leave a
gap at the right edge, and the elastic rule above survives untouched. Which
columns deserve the space is a judgement only the user can make (a shop whose
partners have long legal names wants Partener wide; someone reconciling by
invoice number wants Număr wide), and it is cheap to offer precisely because
the stretch section makes every drag a zero-sum trade. Section sizes are UI
state and persist with the geometry, below.

Window geometry persists across launches through `QSettings` (an `anaf-sync`
/ `tray` scope in the platform-native store — plist, registry, ini),
deliberately *not* `config.toml`: geometry is UI state, not sync
configuration, and a file the design promises to round-trip only on explicit
saves must not churn on every resize. Each window owns a separate key — they
are separate windows with different natural sizes, and remembering one at the
other's dimensions would be a bug, not a convenience — and the table's header
layout rides along in a third (`QHeaderView.saveState()`), for the same reason
and by the same rule. Windows are created lazily and hidden on close, so within
one tray session sizes survive for free; across launches it is Qt's blessed
pair — `saveGeometry()` in `closeEvent` (and on quit) and `restoreGeometry()`
at construction — which also encodes maximised state and pulls a remembered
position back onto a screen that still exists when monitors have detached. A
missing or invalid blob falls back to that window's design size, and its
minimum size holds regardless of what was stored. Tests point `QSettings` at a
throwaway ini file so the suite never touches the real per-user store.

**Dates read the way Romanians write them: `zz.ll.aaaa`.** Every operator-facing
date in the companion — the catalog's Emisă and Încărcată columns, the details pane, the delay
and failure panels, the custom-period fields (`QDateEdit` with
`displayFormat("dd.MM.yyyy")`) — renders as `18.07.2026`. The abbreviated form
the design started from, `18 iul.`, is shorter but drops the year in an archive
that spans them: two rows twelve months apart read identically, and the 60-day
window that makes a date urgent is invisible without it. The numeric form is
also what makes the tabular numerals worth having — every date the same width,
digits aligned in a column, sortable by eye. ISO stays strictly internal: it is
what SQLite stores and orders by, and `{issue_date:%Y-%m-%d}` inside the path
template is a *filename* convention chosen so directory listings sort
chronologically (§4) — a sort key and a display format are different things,
and the UI never borrows one for the other.

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
via `QT_QPA_PLATFORM=offscreen`) before building on each OS; it is a reusable
workflow that `release.yml` calls on a `v*` tag and that can also be dispatched
by hand to check the freeze still works (§9 — it uploads artifacts and never
touches the release itself). Code signing and
notarization are deliberately out of scope for now — the bundles are unsigned
and trigger the usual first-run OS warnings, documented in the README with the
right-click-open workaround; signing is follow-up work before the bundles are
recommended for wide distribution.
