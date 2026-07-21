# Design reference: anaf-sync desktop companion UI (PySide6)

> **Status: built and shipped** (v0.2.0, `src/anaf_sync/tray/`). This file was
> the implementation handoff for the mockup and is kept as the visual
> reference behind it. Where it and the code disagree, the code and
> [DESIGN.md](../DESIGN.md) §10 win.

## Overview
UI for **anaf-sync**, a cross-platform tray tool that archives Romanian e-Factura invoices from ANAF to disk on a schedule. Three deliverables: a tray menu (3 status states), a "Facturi" window (invoice catalog with details pane), and a **separate** "Setări" window (config editor over `config.toml`). Read-only over the archive; its core job is making silent failures visible before ANAF's 60-day purge.

Facturi and Setări are two independent top-level windows, **not** pages of a sidebar-switched stack: the catalog is a surface the user leaves open, Setări is a bounded editing task with an explicit commit boundary (save writes, cancel closes).

## About the Design Files
`mockup/mockup.html` (open in a browser, keep `support.js` next to it) is a **design reference built in HTML** — a clickable prototype showing intended look and behavior. It is NOT production code; it was recreated in PySide6 using Qt idioms with the existing `anaf_sync` package (`config.py`, `state.py`) as the data layer. On-screen anchors: `#1a` tray menu, `#1b` Facturi, `#1c` Setări.

## Fidelity
**High-fidelity.** Colors, spacing, typography, copy, and states are final. Recreate faithfully, but prefer native Qt controls where they match (menus, scrollbars, selects, calendar) over pixel-cloning browser widgets.

## Design Tokens
System font (`system-ui`); monospace (`ui-monospace`/Menlo/Consolas) ONLY for paths, templates, CIFs, identifiers, filenames. Base size 13px; tabular numerals for dates, amounts, CIFs, counts.

Accent (selection/primary actions) is separate from semantic colors.

| Token | Light | Dark |
|---|---|---|
| desk (behind window) | #dfe4ea | #14181d |
| window bg | #f4f6f8 | #1b2128 |
| panel bg | #ffffff | #232a33 |
| border | #d8dee6 | #323c48 |
| border strong | #c4ccd6 | #3d4855 |
| text | #1c2733 | #e4e9ef |
| muted | #5b6b7c | #95a3b3 |
| faint | #8494a5 | #6d7c8c |
| accent | #33658A | #5f92bd |
| accent soft bg | #e3ecf3 | #28394a |
| on-accent | #ffffff | #0f1a24 |
| row hover | #eef2f6 | #28303a |
| row selected | #dfe9f1 | #2c3b4a |
| green / bg | #2E7D46 / #e4f1e9 | #5cb87f / #20332a |
| amber / bg | #B3640F / #f8eedd | #d99b4e / #39301f |
| red / bg | #B3312D / #f8e8e7 | #e07672 / #3c2624 |
| mono chip bg | #eef1f5 | #1d242c |

Radii: window 10px, panels/popovers 8–9px, buttons/fields 6px, pills 9px (full), chips 5–6px. Section spacing 24px; label column in Setări 150px.

## Screens / Views

### 1. Tray menu (`QSystemTrayIcon` + `QMenu`, ~300px)
Tray icon: document glyph + status dot (green/amber/red) — the dot alone must convey state.
Header (not clickable): 9px status dot + bold headline + muted 12px subline:
- **Normal:** "Arhiva este la zi" / "Ultima sincronizare: acum 2 ore · 3 facturi noi"
- **Warning:** "Necesită atenție" / "Ultima sincronizare: acum 2 ore" + amber alert row: "1 factură eșuează repetat — **TERMOENERGIA S.R.L.** — expiră din SPV în **9 zile**"
- **Error:** "Sincronizarea nu funcționează" / "Ultima sincronizare reușită: ieri, 14:32" + red alert row: "Autentificarea ANAF a expirat — rulați `anafpy auth login`" (command in mono chip)

Alert rows: tinted bg (amber-bg/red-bg), semantic text color, 6px radius, 12px font.
Items (all states): "Sincronizează acum" / "Facturi arhivate… 128" (count right-aligned, 55% opacity) / "Deschide dosarul arhivei" / sep / "Setări…" / sep / "Ieșire". Hover: accent bg, on-accent text. Status is always a human sentence, never raw counters. Use a custom `QWidgetAction` for the header + alert row.

### 2. Facturi window (980×620 — the design size, which is also the minimum)
- Native window title "Facturi — anaf-sync". No sidebar, no in-window nav.
- Toolbar row: search field (window bg, 1px border, 6px radius, placeholder "Caută după număr sau partener…") + filter chips **Toate / Primite / Trimise / Probleme (1)** (active: accent bg + on-accent; inactive: window bg, border, muted), then a 1px vertical rule and a **"⚙ Setări…"** button (outlined, muted, hover row-hover) at the right end — it opens the Setări window (§3), the same action as the tray's "Setări…" item.
- Period row (wraps): label "Perioadă" + chips **Luna curentă / Toate / Personalizat…** (active: accent-soft bg + accent border/text). "Personalizat…" reveals two 88px date fields (din–până, `zz.ll.aaaa`, `QDateEdit` with `displayFormat("dd.MM.yyyy")`); focusing either opens a **QCalendarWidget-style popup**: tinted nav bar (◀ "iulie 2026 ▼" ▶ in accent), weekday header lun.–dum. with weekends red, grayed out-of-month days, 30×24 cells. Range selection: first click = start, second = end (auto-close; clicks swapped if reversed); endpoints accent bg, in-between accent-soft.
- Table header: 11px, faint, uppercase, letter-spacing .04em. Columns: Data 84px / Număr 88px / Partener stretch / Direcție 76px / Total 96px right-aligned, gap 8px, row padding 9px 14px, 1px bottom borders. → `QTableView` + model over `state.py`'s `messages` table, `QSortFilterProxyModel` for chips/period/search.
- **Resizable columns.** The four fixed columns are `QHeaderView.ResizeMode.Interactive`, Partener stays `Stretch`: the user drags any header boundary and Partener absorbs the difference, so the table always fills its width. 1px separator at each boundary (border token), `col-resize` cursor. `QHeaderView`'s minimum is **global, not per-section**, so it is one floor of 72px for every column rather than the per-column values a browser could give. The px widths above are a **floor, not a target**: they were measured in a browser at 13px, so the real width is `max(mockup width, fontMetrics().horizontalAdvance(widest value))` plus padding — otherwise a platform with wider metrics clips a date or a total. Cell padding follows the mockup's row model, not Qt's default: 14px at the row's two outer edges and half the 8px column gap between cells (matched by `QHeaderView::section:first` / `:last`), never 14px inside every cell. Section sizes persist across launches next to the window geometry (`QHeaderView.saveState()`/`restoreState()`).
- **Failing row pinned on top**: 3px red inset stripe on the left edge, red text, "eșuată" pill (red-bg/red), em-dashes for număr/total.
- **Delayed invoices (warning)**: every invoice has two dates — *data emiterii* (issue) and *încărcată în SPV* (upload/creation in the system). When upload − issue > **5 days** (configurable threshold), the row gets a 3px **amber** inset stripe and the Data cell turns amber/600. Amber (delayed) is visually distinct from red (failing). Sample delayed row: FF-88214 (emisă 11.07.2026, încărcată 19.07.2026 → 8 zile).
- Pills: 11px, 600 weight, 2px 8px padding, full radius. primită = accent-soft/accent; trimisă = mono-bg/muted + border; eșuată = red-bg/red.
- Selected row: `--sel` bg. Footer status line: "N afișate · 128 în arhivă" + "lista se încarcă pe măsură ce derulați" — **no pagination**; continuous scroll, lazy-load from SQLite.
- Details pane 250px (window bg, left border): invoice number 15px/700, direction pill, key facts as label/value rows (Partener, CIF partener, Data emiterii, Încărcată în SPV, Total), "Fișiere pe disc" mono chips (.zip .pdf), archive path in a mono 10.5px box (mono-bg, word-break), buttons "Deschide PDF" (accent, primary) + "Arată în dosar" (outlined), then provenance under a top border, 11px faint labels: message_id, tip mesaj, arhivat la (values mono/tabular).
- Delayed selection additionally shows an amber panel (amber-bg, 1px amber border, 7px radius) above the key facts: bold "Declarată cu întârziere", then "Emisă 11.07.2026 · încărcată în SPV 19.07.2026 — după 8 zile (limita: 5 zile)".
- Failing selection instead: partner name 15px/700, "eșuată" pill, red panel (red-bg, 1px red border): bold "Descărcarea eșuează repetat", then "Eșuează din **11.07.2026** · **6 încercări** / Ultima eroare: `HTTP 500` / Expiră din SPV în **9 zile**"; red "Reîncearcă acum" button; provenance: message_id 3210447810, tip FACTURA PRIMITA.

### 3. Setări window (760×620 minimum, 1200×780 maximum)
A **second top-level window**, native title "Setări — anaf-sync", opened from the tray's "Setări…" item or the Facturi toolbar button. Facturi stays open and untouched behind it, with its own geometry key.

**Resizable between 760×620 and 1200×780**, and the form re-flows across that whole range. (As built, 760 is a *floor*, not a constant: the window derives its minimum width from the variable reference panel's measured width, so on wide-font platforms it sits higher — see issue #1 and DESIGN.md §10.) The re-flow rules:
- 150px label column, fixed at every size. The field column takes **all** remaining width — no 520px cap.
- *Dosar arhivă* (path field stretching, "Alege…" fixed at the right), *Șablon de denumire*, and the preview box below it each span the full field column. At maximum width a default-length template and its rendered preview each fit on one line, which is the point of allowing the extra width at all.
- *Fișiere salvate* **re-flows on column count**: 3-up (two rows, 3 + 2) until each card would drop below ~170px, then all five on one row — the switch lands at a field column of 882px, i.e. a window of ~1096px. **Four columns never occur**: five cards in four columns strands `metadata` alone on a second row, so the allowed set is {3, 5} only. The grid always fills the field column (no per-card max width — capping it leaves a ragged right edge that breaks alignment with the full-width fields above). At 1200 each card is ~191px and every description but `metadata` fits on one line.
- Two deliberate exceptions to "stretch": the `lookback_days` slider caps at 480px (1–60 over 900px is pixel-hunting, and an over-long slider reads as a progress bar), and help/description text caps at 620px because it is prose and prose has a reading width. Radios, the frequency select and "Alege…" keep their natural size.
- The maximum height is where the form stops scrolling at the *narrowest* width — past it every extra pixel is empty space. At 780 nothing scrolls at any allowed width; at 620 it does. The maximum width is set by the 5-up artifact row: 1100 only just fits five cards, 1200 makes them legible.

Scrollable form, three sections with uppercase 11px faint headers, separated by 1px rules. Every control maps to a `SyncConfig` key — no invented options.

**Companie**
- *CIF-uri urmărite* → `cifs`: **free-entry chips**, not a fixed list — `config.toml` is the source of truth and this form is its editor (see DESIGN.md §10 for why the ANAF authorization inventory is deliberately not wired in). A mono text field ("CIF nou") plus Enter or a "+ Adaugă CIF" button appends; each chip's × removes it. Chips: mono, accent border + accent-soft bg, turning red on hover over the ×. Entries are validated exactly as `config.py` does — strip, upper-case, drop an `RO` prefix, digits only — with inline red errors ("CIF invalid — folosește doar cifre, fără prefixul RO.", "CIF-ul este deja în listă."). The last chip refuses removal (`cifs` min_length=1): "Cel puțin un CIF trebuie să rămână în listă." CIFs already seen in the archive are offered as autocomplete suggestions (`QCompleter`) — a convenience, never a gate. Help: "CIF-urile companiilor pentru care se arhivează facturile — doar cifre, fără prefixul RO. Cel puțin unul rămâne în listă."
- *Direcție* → `direction`: radios Primite / Trimise / Ambele (received/sent/both), default Primite.
- *Fereastră de căutare* → `lookback_days`: slider 1–60, value label "60 zile", help "ANAF păstrează mesajele cel mult 60 de zile."

**Arhivă**
- *Dosar arhivă* → `output.directory`: mono read-only field "~/Facturi" + "Alege…" (`QFileDialog.getExistingDirectory`).
- *Șablon de denumire* → `output.template`: mono editable field, default `{cif}/{direction}/{issue_date:%Y}/{issue_date:%m}/{issue_date:%Y-%m-%d}_{number}_{partner_name}`. **Live preview** below on every keystroke, rendered against a sample invoice:
  - valid → green box: `Previzualizare: ~/Facturi/12345678/received/2026/07/2026-07-03_FCT-1001_ACME CONSTRUCT S.R.L.zip`
  - unknown variable → red box: `Variabilă necunoscută: {numer}`

  Note the preview ends `S.R.L.zip`, not `S.R.L..zip`: `template.py` strips a
  trailing dot from every substituted value because Windows rejects a path
  segment that ends in one. Do not hand-write examples of a sanitiser's output
  — render them.

- **Variable reference panel** (under the preview, inside the same field column).
  A disclosure — `▸ Variabile disponibile (15)` — collapsed by default, expanding
  to a panel-bg card. It exists because the template field is the only control in
  Setări that assumes a vocabulary the UI never shows: today the form teaches that
  vocabulary by punishment (type `{numer}`, get a red box and a dead save button).
  A disclosure rather than a tooltip (which can't be scanned while typing, or
  clicked), a dialog (modal focus steals the caret, killing the edit-preview loop
  that is the whole point), or a permanent panel (~240px of the 620px minimum
  height, for a control most users touch once).
  - Each row is `{name}` (mono chip) · Romanian description · **the value that
    name renders to for the same sample invoice the green box above is using**.
    That third column is the point: the panel is not documentation *about* the
    variables, it is the preview's sample decomposed, so "what will this put in
    my folder name" is answered without a round-trip through the field.
  - Rendered values are produced by `PathTemplate("{name}").render(sample_context())`
    — never written by hand — so the legend cannot disagree with a real sync.
    This is why `{created}` shows `2026-07-06 09-30-00` (`:` is illegal in a path),
    which is the fastest possible argument for `{created:%H%M}`.
  - **Click a row → insert `{name}` at the caret** in the template field, then
    return focus to it; the panel authors, it does not only explain. Rows are
    focusable, so Tab walks the list and Space inserts. Hover: row-hover bg, the
    mono chip flips to accent.
  - An amber **●** marks every variable that comes from the parsed invoice, with
    one footnote: *"● se completează din XML-ul facturii; pentru mesaje fără XML
    (fișiere de eroare, mesaje de la cumpărător) devin `unknown`."* A template
    built only from dotted variables collapses whole classes of message into one
    colliding `unknown/unknown.zip`. `{partner_cif}` keeps the dot even though it
    usually falls back to ANAF's `sender_cif`/`receiver_cif` — "may be unknown"
    is the safe reading, and a third marker state would cost more than it explains.
  - **Expanding never resizes the window**: the list caps at 300px with its own
    scrollbar, and the form's existing `QScrollArea` absorbs the rest, so the
    pinned save bar survives the 620px minimum height. 300 is set just above what
    the 3-column layout needs (~264px measured), so past the breakpoint the whole
    list is visible at once and only the stacked layout scrolls — otherwise the
    wide layout buys nothing. Expanded/collapsed persists in `QSettings` next to
    the window geometry.
  - Reflow: stacked groups → three side-by-side group columns at a field column of
    **882px** — deliberately the same breakpoint as the artifact grid, so the
    window has one reflow moment across its 760–1200 range, not two.
  - The specifier strip does **not** use that breakpoint. It is a row of
    fixed-size examples rather than elastic columns, so it packs as many chips
    per row as measurably fit (up to four). A guessed column count is what made
    the whole form scroll horizontally at 760: four chips are ~1040px wide and
    even two overflow the field column.

  **The 15 valid variables**, grouped as the panel groups them, with the sample
  values it renders. `●` = XML-derived, may be `unknown`.

  | Grup | Variabile |
  |---|---|
  | Factura | ● `{number}` FCT-1001 · ● `{issue_date}` 2026-07-03 · ● `{issue_month}` iulie · ● `{due_date}` 2026-08-02 · ● `{kind}` invoice (sau `credit_note`) · ● `{currency}` RON |
  | Partener | ● `{partner_name}` ACME CONSTRUCT S.R.L · ● `{partner_cif}` 12345670 · `{cif}` 12345678 · `{direction}` received |
  | Mesaj SPV | `{message_id}` 3210447815 · `{request_id}` 4similarid · `{message_type}` FACTURA PRIMITA · `{created}` 2026-07-06 09-30-00 · `{created_month}` iulie |

  Format specifiers live in a strip pinned below a 1px rule at the bottom of the
  card, so they survive scrolling of the list. Two rows — dates and case — because
  every remaining variable is a date or a string:
  - **Date**: `{issue_date:%Y}` → 2026 · `{issue_date:%m}` → 07 · `{issue_date:%Y-%m-%d}` → 2026-07-03 · `{created:%H%M}` → 0930. One faint line says any `strftime` spec works, rather than listing them.
  - **Litere**: `{issue_month!u}` → IULIE · `{issue_month!c}` → Iulie · `{issue_month!l}` → iulie · `{partner_name!t}` → Acme Construct S.R.L. These four are anaf-sync's own conversions (`template.py`) and appear nowhere else in the UI; the strip is their only discoverable home.

  Clicking a specifier example inserts the whole `{issue_date:%Y}` form.

  **Five names were removed from the template context** to get to 15 — `total`,
  `seller_name`, `seller_cif`, `buyer_name`, `buyer_cif`. A path template names a
  document; an amount is a fact *about* it, and `{total}` in a path changes the
  archive location if ANAF ever restates a total. `seller_*`/`buyer_*` are the same
  two parties as `{partner_*}` and `{cif}`, addressed by role instead of by
  relationship — keeping both spellings invites the one template that is silently
  wrong half the time (`{seller_name}` under `direction = both` files *your own*
  company as the folder for every invoice you sent). `{partner_name}` is correct in
  both directions by construction, which is why `context.py` derives it.

  **Implemented.** The template context built by `context.project_message` and
  `preview.sample_context` dropped those five; the projection still derives
  `partner_*` from the seller/buyer parties and still exports `total` in its
  catalog columns for the Facturi table, so only the template-facing dict
  shrank. `tests/test_tray_template_help.py` asserts the panel's name set
  equals `set(sample_context())`, and `test_tray_preview.py` asserts that set
  equals the real template context — so adding a variable to `context.py`
  without documenting it fails the suite instead of quietly producing another
  stale list. This **broke `config.toml` files** that used any of the five
  (they now raise `TemplateError`) — accepted pre-1.0, since the default
  template uses none of them.
- *Fișiere salvate* → `output.artifacts`: checkbox cards in a grid that re-flows 3-up ⇄ 5-up with the field column (never 4 — see above). Names stay in English mono; descriptions Romanian 11px faint: **zip** "arhiva semnată originală" ✓, **pdf** "redarea oficială ANAF" ✓, **xml** "XML-ul UBL al facturii", **signature** "semnătura MF detașată", **metadata** "fișier JSON cu detaliile mesajului". Checked card: accent border + accent-soft bg.

**Programare**
- *Frecvență*: select — La fiecare oră / 3 ore / **6 ore** (default) / 12 ore / O dată pe zi.
- Status line: green dot + green 12.5px "Activă — următoarea rulare: marți, 21.07.2026, 06:00".

**Footer save bar** (pinned, window bg, top border): note "Modificările se scriu în `config.toml` — fișierul rămâne editabil manual" + "Renunță" (outlined) / "Salvează modificările" (accent primary).

**Both buttons close the window.** "Renunță" discards every pending edit and closes without touching `config.toml`; Esc and the window close button do exactly the same thing (it is the `QDialog` reject role, so wire all three to one slot). "Salvează modificările" writes `config.toml` and closes. Closing with unsaved edits needs no confirmation prompt — nothing outside this window depends on the pending state, and the file is the source of truth either way. Reopening always re-reads `config.toml`, so a cancelled session leaves no residue.

## Interactions & Behavior
- Table row click → selects (sel bg) and swaps details pane content; failing row swaps to the red panel; delayed rows add the amber panel.
- Delay highlight is conditional: delayed = (upload date − issue date) > threshold (5 days — the `health.DELAY_THRESHOLD_DAYS` constant; promoting it to a config key stays parked until asked).
- Filter chips, period chips, calendar range, CIF chips, radios, artifact cards, slider, select: all stateful as described; filters combine (direction ∧ period).
- Column resize: dragging a header boundary re-proportions that column and Partener absorbs the difference; the boundary tracks the pointer in both directions. Widths survive the session and the next launch.
- Hovers: menu items → accent bg; table rows → hover bg; header boundaries → accent separator. No animations required; instant state changes are fine (desktop feel).
- Buttons "Deschide PDF"/"Arată în dosar" → open file / reveal in file manager. "Reîncearcă acum" → trigger a sync for that message.
- Template preview re-renders per keystroke; unknown `{var}` → error state, save should be blocked while invalid.
- Variable panel: the disclosure toggles on click and on Space/Enter; clicking a variable or a specifier example splices its text at the template field's caret (replacing any selection) and re-renders the preview on the same path as typing.

## State Management
Runtime state: selected message_id; filter chip; period (+ custom from/to); delay threshold (days); template text (+ validity); CIF list (free entry, min 1); direction; lookback_days; artifact set (min 1); frequency. Persisted via `SyncConfig` → `config.toml`. Catalog/failures read from `state.py`'s SQLite (`messages`, `failures` tables). Tray state = f(failures, auth): any failure → amber; auth expired/sync broken → red; else green.

## Sample Data
| Emisă | Încărcată în SPV | Număr | Partener | Direcție | Total | Stare |
|---|---|---|---|---|---|---|
| 11.07.2026 | — | — | TERMOENERGIA S.R.L. | eșuată | — | failing (red) |
| 18.07.2026 | 18.07.2026 | FCT-2107 | ELECTROMONTAJ CARPAȚI S.R.L. | primită | 4.821,50 RON | |
| 17.07.2026 | 17.07.2026 | 2026-0713 | DISTRIGAZ VEST S.A. | primită | 1.245,00 RON | |
| 15.07.2026 | 16.07.2026 | AS-1042 | MOBILA PRODEX S.R.L. | trimisă | 12.400,00 RON | |
| 11.07.2026 | 19.07.2026 | FF-88214 | BIROTICA PLUS S.R.L. | primită | 386,75 RON | delayed (amber) |
| 03.07.2026 | 06.07.2026 | FCT-1001 | ACME CONSTRUCT S.R.L. | primită | 2.480,00 RON | |

Table "Data" column shows the issue date.

Romanian number format (1.234,56 RON), **Romanian dates everywhere: `zz.ll.aaaa`** ("18.07.2026" — never an abbreviated month, never ISO), correct diacritics everywhere. ISO stays internal: it is what the catalog stores and sorts by, and `{issue_date:%Y-%m-%d}` inside the path template is a *filename* convention, not a display format — do not conflate the two. Never translate code identifiers ({cif}, zip, pdf, config.toml, anafpy auth login).

## Suggested Qt mapping
QSystemTrayIcon + QMenu (QWidgetAction header/alert) · two independent top-level windows — QMainWindow for Facturi (980×620 min, no max), QDialog/QWidget for Setări (760×620 min, 1200×780 max) · custom QLayout subclass (flow-layout style) for the artifact cards, no QStackedWidget · QLineEdit (search) · QToolButton checkable chips · QTableView + QAbstractTableModel + QSortFilterProxyModel · custom QStyledItemDelegate for pills + red stripe · QHeaderView with Interactive sections + one Stretch section · details pane QWidget/QFrame · QDateEdit + calendar popup (QCalendarWidget subclass for range) · QRadioButton, QSlider, QComboBox, QCheckBox cards · variable panel as a `QToolButton` (`ArrowType.Right/Down`) over a `QFrame` card, rows as flat focusable `QToolButton`s, `QLineEdit.insert()` for the caret splice · QSS themed from the token table (light/dark via two QSS sheets or QPalette).

## Out of scope — do not add
No login/credential UI (auth is the anafpy CLI), no delete/edit/upload, no charts/dashboards, no environment switcher, no onboarding, no pagination.

## Files
- `mockup/mockup.html` — the single clickable reference (open in a browser; `support.js` must sit alongside). Anchors: #1a tray, #1b Facturi, #1c Setări. A **light/dark picker sits at the top of the canvas** — mockup chrome, not product UI — so the file is reviewable in a plain browser and not only inside the design tool's Tweaks panel, which drives the same `theme` prop. The Tweaks panel also carries `templateError` (preview error state) and `variablePanel` (variable reference expanded/collapsed).
