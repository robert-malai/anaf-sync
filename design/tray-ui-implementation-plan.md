# Implementation plan — anaf-sync desktop tray companion

Executable plan for the UI described in [`design/README.md`](README.md) (the
design handoff — **the source of truth for all visuals, copy, and behaviour**)
and its clickable reference (`design/mockup/anaf-sync Mockup.dc.html`, open in
a browser with `support.js` alongside; anchors `#1a` tray, `#1b` Facturi,
`#1c` Setări).

Read before starting: `CLAUDE.md` (conventions, invariants, gates),
`DESIGN.md` (architecture rationale), the handoff README. anafpy's installed
source under `.venv/lib/python3.13/site-packages/anafpy/` is the spec for the
upstream API.

## Product decisions already made (do not re-litigate)

- The companion lives in **this repo** as subpackage `anaf_sync.tray`, behind
  an optional dependency extra `tray` (PySide6 + tomlkit), with its own
  console script `anaf-sync-tray`. Core `anaf-sync` stays GUI-free.
- The tray app is a **read-only observer** of `state.db` plus an editor of
  `config.toml`. It never downloads, uploads, deletes, or rewrites archive
  files. Sync is always performed by spawning the `anaf-sync sync` CLI.
- Config writes use **tomlkit round-trip** so user comments/formatting in
  `config.toml` survive GUI saves.
- Delivery is **incremental**: milestones M0–M4 below, each landing with all
  four gates green and each usable on its own. Work happens on branch
  `feature/tray-ui`; one PR per milestone into it (or sequential commits if
  solo), merged to `main` only at milestone boundaries.
- Scope guard (from the handoff): no login/credential UI, no delete/edit/
  upload of invoices, no charts, no environment switcher, no onboarding, no
  pagination. Every Settings control maps to an existing `SyncConfig` key.

## Ground rules for the implementing model

- Gates, run from the repo root; all four must pass at every commit:
  `uv run pytest -q` · `uv run ruff check src tests` ·
  `uv run black --check src tests` · `uv run mypy src` (strict).
- Respect `CLAUDE.md` invariants verbatim (auth is anafpy's; idempotence;
  path safety through `template.py`; error philosophy; cross-platform).
- Qt code stays at the edges. Anything with logic (state derivation, query
  building, config IO, formatting) lives in pure modules with full type hints
  and unit tests; Qt widgets are thin assemblies over them. Expect
  `mypy --strict` to hold everywhere; isolated `# type: ignore[...]` is
  acceptable only at Qt signal/slot friction points.
- All operator-facing strings are **Romanian with correct diacritics** and
  live in `anaf_sync/tray/strings.py` only — transcribe them **exactly** from
  the handoff README (§Screens, §Sample Data). Code identifiers
  (`{cif}`, `zip`, `config.toml`, `anafpy auth login`) are never translated.
  Everything developer-facing stays English.
- Visual tokens (colors, radii, spacing, font sizes, light+dark) come from
  the handoff's token table. Implement them once, in `tray/theme.py`, as
  named constants + QSS generation; no hex literals scattered in widgets.

---

## M0 — core enablers (no Qt; touches `state.py`, `engine.py`, `cli.py`, `context.py`)

Everything the UI reads but that doesn't exist yet. Ships as a normal core
change, useful to the CLI on its own.

### M0.1 Schema v2: persist the SPV upload time

The delayed-invoice feature (amber state) needs both dates per invoice:
*issue date* (`issue_date`, already stored) and *SPV upload time* (ANAF's
`data_creare`, parsed by `context._parse_created` but currently dropped).

- `state.py`: add `created_at TEXT` (ISO datetime, nullable) to the
  `messages` table. Bump `schema_version` to `2`; in `Archive.open`/
  `_init_schema`, migrate v1 → v2 with `ALTER TABLE messages ADD COLUMN`
  (existing rows keep NULL). Add `created_at: dt.datetime | None = None` to
  `CatalogEntry`.
- `context.py`: `catalog_fields()` additionally returns `created` (the parsed
  `dt.datetime | None`) so the engine can fill `CatalogEntry.created_at`.
  Keep `build_context` unchanged.
- `engine.py`: thread the new field through when constructing `CatalogEntry`.
- Tests: migration test (open a v1 db file fixture → v2 schema, old rows
  readable), round-trip of `created_at` through `record()`.

### M0.2 Concurrent reads: WAL + read-only open

The tray reads while a scheduled sync writes.

- `Archive._init_schema` (or `open`): `PRAGMA journal_mode=WAL`.
- New `Archive.open_readonly(path) -> Self`: connect with
  `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`, no schema init, no
  pruning; raise a clear error if the file doesn't exist.
- Test: writer inserts while a read-only connection queries; no
  `database is locked` within a normal busy timeout.

### M0.3 Last-run record

- `state.py`: `RunRecord` (pydantic) — `finished_at: dt.datetime`,
  `outcome: Literal["ok", "failed", "crashed"]`, `listed: int`, `archived: int`,
  `failures: int`, `error: str | None` (one-line summary),
  `error_kind: str | None` (exception class name, e.g. `AnafAuthError`).
  `Archive.record_run(RunRecord)` / `Archive.last_run() -> RunRecord | None`,
  stored as JSON under `meta` key `last_run`.
- `cli.py` `sync` command: write the record on every path — success
  (`outcome="ok"` or `"failed"` when the report has failures), caught
  boundary exceptions (`"failed"` with `error_kind`), and the system-mode
  excepthook (`"crashed"`). Never let a `record_run` error mask the original
  failure (wrap in try/except, log).
- Tests: each outcome path via the existing fake-client engine tests +
  a CLI-level test.

### M0.4 Health helpers — `src/anaf_sync/health.py` (new module)

Pure functions shared by `anaf-sync status` and the tray:

- `days_until_purge(failure: FailureRecord, now: dt.datetime) -> int` —
  days until ANAF's 60-day window closes on a failing message, derived from
  `first_failed_at` (message entered SPV at or before first failure; document
  the approximation — if M0.1's `created_at` is available for the message id
  use it, else fall back to `first_failed_at`).
- `upload_delay_days(issue: dt.date | None, created: dt.datetime | None) -> int | None`
  — whole days between issue and SPV upload; `None` when either is missing.
- `DELAY_THRESHOLD_DAYS: int = 5` — single constant (handoff §Interactions:
  "make it a constant or config-derived"; constant now, config later only if
  asked).
- Tray/status state derivation:
  `derive_health(last_run, failures, now) -> Health` where `Health` carries
  `state: Literal["ok", "warn", "err"]` plus the data the menu needs (counts,
  worst failure with partner/days-left, auth-broken flag). Rules (handoff
  §State Management): any failure record → `warn`; last run `crashed`, or
  `failed` with `error_kind` in the auth/config family (`AnafAuthError`,
  `AnafConfigError`), or no successful run within 2× the scheduled interval →
  `err`; else `ok`. `err` wins over `warn`.
- `Archive.catalog()` query API (in `state.py`):
  `catalog(*, search: str | None = None, direction: str | None = None,
  issued_from: dt.date | None = None, issued_to: dt.date | None = None,
  limit: int = 100, offset: int = 0) -> list[CatalogEntry]`, ordered by
  `issue_date DESC, message_id DESC` (NULL issue dates last). `search`
  matches `number` or `partner_name` (case-insensitive LIKE). Plus
  `catalog_count(same filters) -> int`. SQL-side filtering is the lazy-load
  mechanism — do **not** load the whole table into a proxy model.
- `anaf-sync status` gains the purge warning lines (reuses
  `days_until_purge`) — DESIGN.md §9's "about to age out" item, done.
- Tests: exhaustive unit tests over `health.py` (each state rule, boundary
  days), catalog filter combinations, ordering, offset/limit.

### M0.5 Bookkeeping

- DESIGN.md: add a short section "The desktop companion" — read-only
  observer over `state.db`, three states, spawns the CLI for sync, schema v2
  rationale.
- Acceptance: gates green; `anaf-sync status` shows last run + purge
  warnings; a v1 `state.db` migrates in place.

---

## M1 — tray indicator (`anaf_sync.tray`, first Qt code)

### Packaging

- `pyproject.toml`:
  - `[project.optional-dependencies] tray = ["pyside6>=6.7", "tomlkit>=0.13"]`
  - `[project.scripts] anaf-sync-tray = "anaf_sync.tray.app:main"`
  - dev group: add `pytest-qt>=4.4`.
- `anaf_sync/tray/__init__.py` guards the PySide6 import: on
  `ImportError`, `main()` prints
  `instalați cu: pip install "anaf-sync[tray]"` to stderr and exits 1 —
  core CLI must keep working without Qt installed.
- CI/tests run Qt with `QT_QPA_PLATFORM=offscreen` (set in the pytest-qt
  tests or `tests/conftest.py` when PySide6 is importable; skip tray tests
  with `pytest.importorskip("PySide6")` so the suite passes without the
  extra).

### Modules

| File | Contents |
|---|---|
| `tray/strings.py` | Every RO string from handoff §1 (three states, alert rows, menu items) as constants/format functions. Relative-time formatting ("acum 2 ore", "ieri, 14:32") implemented here, pure, tested. |
| `tray/theme.py` | Token table (light+dark dataclasses) from handoff §Design Tokens; QSS builders for menu/window; scheme detection via `QGuiApplication.styleHints().colorScheme()` + change signal. |
| `tray/status.py` | Bridges core → UI: opens `Archive.open_readonly`, loads config (tolerating a broken/missing config → `err` state with message), calls `health.derive_health`, returns a display model (icon state, headline, subline, alert text). Pure, tested with tmp databases. |
| `tray/icons.py` | `QIcon` painted at runtime (QPainter): document glyph + status dot per state, sizes 16/22/32; macOS: also a template (monochrome) variant so the menu bar tints correctly, dot stays colored. |
| `tray/runner.py` | `SyncRunner` (QObject): spawns `anaf-sync sync` via `QProcess` (resolve the console script the way `scheduling.py` resolves it); signals `started/finished(exit_code)`; ignores a second start while running (the file lock in `lock.py` also serialises against scheduled runs — document that the UI-level guard is cosmetic). |
| `tray/watcher.py` | `StateWatcher` (QObject): `QFileSystemWatcher` on `state.db` and `state.db-wal` + 500 ms debounce timer + 60 s poll fallback; signal `changed`. Re-adds paths after atomic replaces (watchers drop renamed files). |
| `tray/app.py` | `main()`: QApplication (`setQuitOnLastWindowClosed(False)`), single-instance guard (reuse `filelock` on a tray-specific lock file; second launch exits 0 quietly), builds `QSystemTrayIcon` + menu, wires watcher → status refresh → icon/menu update. |

### Menu (handoff §1 — copy and layout are exact)

- Header + alert row as a non-interactive `QWidgetAction` (status dot,
  bold headline, muted subline; amber/red alert row only in those states;
  `anafpy auth login` rendered as a mono chip).
- Items: *Sincronizează acum* → `SyncRunner` (item shows a spinner/disabled
  "Se sincronizează…" while running, then refreshes); *Facturi arhivate…*
  with right-aligned count (opens the main window in M2 — until then, hidden
  or disabled); *Deschide dosarul arhivei* →
  `QDesktopServices.openUrl(output.resolved_directory)`; *Setări…* (M3;
  hidden until then); *Ieșire*.

### Acceptance (M1)

- `uv run anaf-sync-tray` on macOS shows the icon; dot reflects a seeded
  `state.db` (ok/warn/err each verified by editing the db in tests/dev).
- Completing a `anaf-sync sync` in another terminal updates the menu within
  ~1 s (watcher) without restarting the tray.
- All gates green; `uv sync` without the extra still builds and tests.

---

## M2 — main window, Facturi view (handoff §2)

### Modules

| File | Contents |
|---|---|
| `tray/format.py` | RO formatting, pure + tested: money `4.821,50 RON` (`QLocale("ro-RO")` or manual — must match handoff exactly), short dates `18 iul.` (reuse month names — note `context._RO_MONTHS` is full-month; add abbreviated forms here), tabular provenance values. |
| `tray/models.py` | `CatalogModel(QAbstractTableModel)` over `Archive.catalog()`: SQL-side filters (search/direction/period), `fetchMore`/`canFetchMore` paging (page 100) — continuous scroll, **no pagination UI**; failing messages (from `Archive.failures()`) synthesized as pinned rows *above* row 0 of the catalog with a `role` marking `failing`; custom roles: `Failing`, `Delayed` (via `health.upload_delay_days` > threshold), `MessageId`. Filter changes reset the model. |
| `tray/delegates.py` | `QStyledItemDelegate`: direction pills (primită/trimisă/eșuată per token table), 3px red inset stripe (failing) / amber stripe + amber Data cell (delayed). |
| `tray/calendar.py` | Range calendar popup: `QCalendarWidget` subclass or custom grid per handoff (nav bar, lun.–dum. header with red weekends, 30×24 cells); click 1 = start, click 2 = end, auto-close, swap if reversed; endpoints accent, in-between accent-soft. Emits `range_selected(from, to)`. |
| `tray/details.py` | Details pane widget with three variants — normal (facts, *Fișiere pe disc* mono chips, path box, *Deschide PDF* primary + *Arată în dosar*, provenance block), delayed (adds amber "Declarată cu întârziere" panel), failing (red panel, *Reîncearcă acum*) — content per handoff §2, strings from `strings.py`. |
| `tray/window.py` | `MainWindow` (fixed 980×620): 38px titlebar, 148px sidebar (Facturi badge / Setări) → `QStackedWidget`; toolbar (search `QLineEdit`, chips as checkable `QToolButton`s *Toate/Primite/Trimise/Probleme (n)*); period row (*Luna curentă* default / *Toate* / *Personalizat…* revealing two `QDateEdit`s + range calendar); `QTableView` (columns Data 52 / Număr 88 / Partener flex / Direcție 76 / Total 96 right-aligned); footer "N afișate · M în arhivă · lista se încarcă pe măsură ce derulați"; details pane 250px right. |

### Behaviour

- Row selection swaps the details pane variant. Filters combine
  (direction ∧ period ∧ search); *Probleme* shows failing (and delayed —
  match the mockup's behaviour, check `#1b`) rows only.
- *Deschide PDF* / *Arată în dosar*: build the artifact path from
  `CatalogEntry.base_path` + artifact extension under
  `output.resolved_directory`; open via `QDesktopServices`; reveal-in-folder
  uses platform dispatch (macOS `open -R`, Windows `explorer /select,`,
  Linux: open the containing dir). Missing file → disable button with
  tooltip "fișierul nu a fost găsit pe disc".
- *Reîncearcă acum* = `SyncRunner` (a full sync retries every failure by
  design; do not build per-message download).
- Tray menu item *Facturi arhivate…* now opens/raises this window
  (window is created lazily, closed = hidden, app keeps running in tray).

### Acceptance (M2)

- Against a seeded `state.db` (fixture builder producing the handoff's
  §Sample Data exactly), the view visually matches the mockup `#1b` in both
  themes; failing row pinned; FF-88214 shows amber delayed treatment
  (emisă 11 iul., încărcată 19 iul.).
- Model unit tests: pinning, roles, filter combinations, fetchMore paging,
  delayed computation boundary (exactly 5 days → not delayed; 6 → delayed).
- pytest-qt: selection → details swap; chip toggling updates footer counts.

---

## M3 — Setări view (handoff §3)

### Modules

| File | Contents |
|---|---|
| `tray/config_io.py` | tomlkit round-trip, pure + heavily tested: `load(path) -> tomlkit.TOMLDocument`, `apply(doc, form: SettingsForm) -> None` (mutates only changed keys, preserves comments/order), `save(doc, path)` — atomic temp-file + `os.replace` in the same dir (the codebase's pattern). `SettingsForm` (pydantic) mirrors the editable keys; validation = `SyncConfig.model_validate(doc_as_dict)` **before** writing; validation errors surface in the UI, file untouched. Test: the `init` template's comments survive load→apply→save byte-for-byte except changed values. |
| `tray/preview.py` | Template live preview, pure: renders the user's template with the **real** `template.py` code path against a fixed sample context built from `context.py`'s variable set (sample values from the handoff: FCT-1001 / ACME CONSTRUCT S.R.L. / 2026-07-03 / cif 12345678, received). Valid → the green-box path string (prefixed `~/Facturi/…`, suffixed `.zip`); unknown variable → the exact RO error "Variabilă necunoscută: {name}". Never reimplement rendering or validation — call the production code and map its exception. |
| `tray/settings_view.py` | The form per handoff §3: 150px label column, three sections. **Companie**: CIF multiselect chips (see decision below), direction radios, lookback slider 1–60 + value label. **Arhivă**: read-only dir field + *Alege…* (`QFileDialog.getExistingDirectory`), template field (mono) + live preview box (green/red), artifact checkbox cards 3-col grid (names EN mono, descriptions RO). **Programare**: frequency `QComboBox` (La fiecare oră / 3 ore / 6 ore / 12 ore / O dată pe zi) + green status line from `scheduling.status()`. Footer save bar pinned: note + *Renunță* / *Salvează modificările*. |

### Decisions encoded

- **CIF list source**: anafpy exposes **no** authorized-CIF listing API
  (verified against anafpy's installed source). Deviation from the
  handoff, to be revisited if anafpy grows such an API: the fixed choice
  list = union of `SyncConfig.cifs` (configured) and
  `SELECT DISTINCT cif FROM messages` (seen in the archive), rendered as the
  handoff's multiselect chips; plus a small "+ Adaugă CIF" free-entry that
  validates digits-only (mirroring `SyncConfig`'s validator). At least one
  CIF stays selected (`min_length=1`) — last checked chip refuses to
  uncheck, per the handoff.
- Save is **disabled** while: template preview is in error state, zero
  artifacts checked, or zero CIFs selected. Tooltip explains which.
- Frequency maps to `scheduling.py`'s interval presets. On save, if
  frequency changed and a schedule is installed, re-install it via
  `scheduling.py`'s existing functions (never shell out to schtasks/systemctl
  directly from the tray). "Dezactivată" is not in the combo — removing the
  schedule stays a CLI concern this milestone.
- After a successful save: toast/status flash + tray status refresh; the
  running config in the tray process is reloaded from disk (no cached copy).

### Acceptance (M3)

- Edit template to `{numer}` → red box with exact copy, save disabled;
  fix → green box shows the sample path; save → `config.toml` diff touches
  only changed keys, comments intact (assert in test).
- Round-trip test on the `init`-generated file; slider/radios/chips/cards
  all persist correctly; `mypy --strict` clean.

---

## M4 — distribution

### M4.1 Autostart: `anaf-sync tray install|remove|status`

New cyclopts sub-app in `cli.py` (pattern-match `schedule`), dispatching in
`scheduling.py` or a sibling `autostart.py`:

- macOS: LaunchAgent `~/Library/LaunchAgents/ro.anaf-sync.tray.plist` —
  `RunAtLoad`, no `KeepAlive`, `ProcessType Interactive`.
- Windows: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value
  `anaf-sync-tray` (via `winreg`).
- Linux: `~/.config/autostart/anaf-sync-tray.desktop`.
- Executable path resolved at install time exactly like
  `scheduling.py` does for `anaf-sync` (console script; must also work when
  frozen — `sys.frozen` → `sys.executable`).
- `install` is idempotent; `status` prints the platform-native state.
- Tests: unit-test the generated plist/desktop/registry payloads (dry
  builders returning strings/dicts), platform calls behind the same seam
  `scheduling.py` uses.

### M4.2 Bundles: PyInstaller + CI

- `packaging/tray.spec` (one spec, platform conditionals): entry
  `anaf_sync/tray/app.py`; windowed/noconsole; macOS `.app` with
  `LSUIElement=1` (menu-bar only, no Dock icon), bundle id
  `ro.anaf-sync.tray`; Windows one-dir windowed exe; Linux one-dir binary.
  Exclude unused Qt modules (QtWebEngine, QtQml, …) to keep size sane.
- GitHub Actions workflow `release-tray.yml`: matrix
  `[macos-latest, windows-latest, ubuntu-latest]`, `uv sync --extra tray`,
  run gates, build with PyInstaller, smoke-run `--help`/version of the
  binary where headless-possible, upload artifacts; attach to GitHub
  Releases on tag.
- **Known gap, explicitly out of scope**: code signing + notarization
  (macOS) and Authenticode (Windows). Unsigned bundles show OS warnings;
  document the right-click-open workaround. Follow-up work before bundles
  are recommended to operators.

### M4.3 Operator docs

- `README.md` (**Romanian**, operator-facing): new section — what the icon
  colors mean, install via `pip install "anaf-sync[tray]"` or the release
  bundle, `anaf-sync tray install`, Linux/GNOME AppIndicator-extension note.
- `DESIGN.md`: autostart + bundling rationale appended to the companion
  section.

---

## Cross-cutting checklists

**Definition of done, every milestone:** four gates green · no new hex
literals/strings outside `theme.py`/`strings.py` · both themes verified ·
works with the `tray` extra absent (core) and present (UI) · DESIGN.md
updated when behaviour-level decisions land.

**Manual smoke per milestone (dev machine, macOS):** seeded db → three tray
states; live sync in a second terminal → tray refresh; M2: mockup `#1b`
side-by-side; M3: save → `git diff`-style inspection of `config.toml`.

**Open questions parked (do not solve unprompted):**
1. Authorized-CIF listing from ANAF — waiting on anafpy; fallback shipped in
   M3 stands.
2. Delay threshold as a config key (`delay_threshold_days`) — constant for
   now; adding a key requires updating `SyncConfig`, `init` template, README.
3. Signing/notarization — separate branch after M4.
4. Per-message retry (vs full sync) — deliberately rejected; a sync retries
   everything failed by design.

## Milestone sizing (rough, for planning)

M0 smallest (pure Python, well-trodden modules) → M1 small-medium (first Qt,
packaging edges) → M2 largest UI surface (model/delegates/calendar) →
M3 medium (config_io + form, riskiest correctness: TOML round-trip) →
M4 medium (platform plumbing + CI, little Qt). M4.1 can start any time after
M1; M4.2 after M1 in degraded form (icon-only app), fully after M3.
