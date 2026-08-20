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

### 2. Facturi window (1160×620 design size; the width floor is *derived* — see below)
- Native window title "Facturi — anaf-sync". No sidebar, no in-window nav.
- **The minimum width is derived, not constant.** 1160×620 is the design size, but each fixed section sizes itself from the platform's own font (its label plus the header's two marks), which on most desktops runs wider than the px measured in a browser — ~724px of fixed columns here against the 630 the mockup's numbers add up to. A constant minimum would therefore squeeze **Partener** hardest on exactly the machines whose metrics are widest, so the window floors its width at `sum(fixed sections) + 200 + margins + details pane`, i.e. it guarantees Partener 200px with the pane open. Setări derives its own floor the same way (DESIGN.md §10, issue #1). Expect ~1210 on a stock macOS font.
- Toolbar row: search field (window bg, 1px border, 6px radius, placeholder "Caută după număr sau partener…"), then a 1px vertical rule and a **"⚙ Setări…"** button (outlined, muted, hover row-hover) at the right end — it opens the Setări window (§3), the same action as the tray's "Setări…" item. **No filter chips and no period row**: every filter lives on the column it filters (below). Search stays in the toolbar because it is the one filter that spans two columns — it matches `number` **or** `partner_name` — so it has no single header to belong to.
- **Active-filter bar** (only present when something is filtered; zero height otherwise): "Filtre active" + one removable label per active filter (accent-soft bg, accent border/text, `×` at the right) + a "Șterge toate filtrele" link. This band is not decoration — a filter shut inside a header popover is invisible, and without it the honest question "why is this invoice missing?" has no answer on screen. It wraps to a second line rather than eliding.
- Table header: 11px, faint, uppercase, letter-spacing .04em. Columns: Emisă 84px / Încărcată 88px / Număr 88px / Partener stretch / **De la CIF 96px** / **Pentru CIF 102px** / Direcție 76px / Total 96px right-aligned, gap 8px, row padding 9px 14px, 1px bottom borders. Both date columns render `zz.ll.aaaa` tabular; **Încărcată** is the SPV upload date (`created_at`), sitting next to Emisă so a delay is read by comparing neighbours — em-dash when unknown (backfilled rows). "Data" was renamed **Emisă** the moment a second date appeared: one column called "date" next to another date is ambiguous.
- **Both CIFs, as roles rather than sides.** *De la CIF* is the issuer and *Pentru CIF* the recipient, so which one holds the followed CIF depends on `direction`: on a received invoice the partner issues and you receive, on a sent one they swap. Absolute roles beat "CIF" plus "CIF partener" because the reader never has to hold the direction in their head and do the substitution — the table reads as a flow, which is how invoices are actually reasoned about. **The followed CIF renders at full `--text` strength and the counterparty's at `--muted`**, so which side of the flow you are on is legible without reading digits. The details pane carries the same two rows, replacing the old "CIF partener".
- The partner side is best-effort — anafpy scrapes `partner_cif` out of the `detalii` prose — so it is em-dash on rows where the listing did not carry it, and blanks sort last in both directions like every other blank.
- **These two are derived columns, not stored ones.** There is no `de_la_cif` in `messages`; both are `CASE direction WHEN 'sent' THEN cif ELSE partner_cif END` (and its mirror). That expression sorts and filters in SQL perfectly well, just unindexed — fine at archive sizes, and if it ever is not, SQLite ≥3.31 takes a `GENERATED ALWAYS AS (…) STORED` column with an index on it (a schema v3 migration, additive like v2's).
- **The header is the control surface: the label sorts, the ▽ filters, the boundary resizes.** Three hit targets in one cell, so each answers the pointer on its own: the section takes a `--hover` background and its label brightens to `--text` (QSS `QHeaderView::section:hover`, which needs `WA_Hover`), the ▽ turns full accent when the pointer is on *it* rather than on the label, and the boundary shows the `col-resize` cursor.
  - *Sort*: click a column label to sort by it, click again to reverse. Only the sorted column draws a caret, but *every* section reserves the room for one: the header's right padding is a constant (`MARKS_WIDTH` — both marks plus their gaps) and each fixed column is sized to fit its label plus that padding, so sorting a column never shifts its label sideways. Both marks are drawn as polygons, not typed as `▲`/`▽`: those glyphs are missing from enough UI fonts to render as tofu boxes on some Windows and Linux desktops. Default is **Emisă ↓** — the order the catalog already emits. **Direcție does not sort**: it holds three values, so a filter answers everything a sort on it could. Blanks sort last in *both* directions.
  - *Filter*: click the **▽** to open that column's popover. The ▽ is always visible, faint (opacity .6) until the column is filtered and then solid accent **▼**; a funnel that only appears on hover is a filter nobody finds. **Total carries no ▽** — it sorts but does not filter (an amount range is a form, not a popover) — which is also what tells the eye the ▽ is per-column and not chrome.
  - *Popover kinds*: **CIF** on De la CIF and Pentru CIF (a "CIF-ul conține…" field, then the followed CIFs under a "CIF-urile tale" heading as one-click shortcuts with their counts). Deliberately **not** a checklist: only the followed CIFs are a bounded set (`Archive.distinct_cifs()`) — the counterparty side is every company that ever invoiced you, so a checklist would be hundreds of rows long. The shortcuts keep the one-click case, "which of my entities is this", which is why the column was added at all. **date** on Emisă and Încărcată (radio list Toate / Luna curentă / Ultimele 3 luni / Personalizat…, the last revealing two 88px `zz.ll.aaaa` fields and the calendar below them); **text** on Număr and Partener (one "conține…" field); **checklist** on Direcție (one row per value with its archive count, unchecking filters it out). Every popover ends in a "Șterge filtrul" link. Filters apply live — no OK button — and unchecking the *last* value of a checklist is refused, exactly as the CIF list in Setări refuses its last chip.
  - The calendar is unchanged, only relocated: it now lives inside the date popover instead of under a period row. Tinted nav bar (◀ "iulie 2026 ▼" ▶ in accent), weekday header lun.–dum. with weekends red, grayed out-of-month days, 30×24 cells, first click = start, second = end (swapped if reversed), endpoints accent bg, in-between accent-soft.
  - **"Doar declarate cu întârziere"** is a checkbox inside the *Încărcată* popover, under a separator — the delay is a fact about the upload date, so it belongs to the column the amber highlight already sits on, not in a general-purpose "Probleme" bucket. Failing rows are reachable the same honest way: "eșuată" is a value in the Direcție checklist.
  - A popover opens under its section's left edge and is then **clamped onto the screen** — pushed left when it would overhang the right edge, and flipped above the header when it would fall off the bottom. Clamping rather than anchoring each column by hand is what makes the two right-hand columns safe at any window width, and survives the user resizing a column. Clicking anywhere outside closes it (`Qt.Popup`).
- **Resizable columns.** The seven fixed columns are `QHeaderView.ResizeMode.Interactive`, Partener stays `Stretch`: the user drags any header boundary and Partener absorbs the difference, so the table always fills its width. 1px separator at each boundary (border token), `col-resize` cursor. `QHeaderView`'s minimum is **global, not per-section**, so it is one floor of 72px for every column rather than the per-column values a browser could give. The px widths above are a **floor, not a target**: they were measured in a browser at 13px, so the real width is `max(mockup width, fontMetrics().horizontalAdvance(widest value), fontMetrics().horizontalAdvance(UPPERCASED LABEL) + MARKS_WIDTH)` plus padding — three floors, because missing the third clips a header label under its own controls — otherwise a platform with wider metrics clips a date or a total. Cell padding follows the mockup's row model, not Qt's default: 14px at the row's two outer edges and half the 8px column gap between cells (matched by `QHeaderView::section:first` / `:last`), never 14px inside every cell. Section sizes persist across launches next to the window geometry (`QHeaderView.saveState()`/`restoreState()`) — and so does the sort indicator, which rides in the same blob for free. The stored blob is versioned by key: the two role CIFs make it an **eight**-section header, so it moved to `facturi/header/v3` rather than replay a six-section layout onto it. `restoreState` also restores Qt's own sort-indicator flag, which this header paints itself — so it is switched back off after a restore, and the model is told the order it was just handed (a blob equal to the live one emits no signal).
- **Failing row pinned on top, whatever the sort**: 3px red inset stripe on the left edge, red text, "eșuată" pill (red-bg/red), em-dashes for încărcată/număr/total. They are pinned rather than sorted because they carry almost no values to sort by, and being seen first is their entire purpose. They *do* obey the filters, and broadly: a failing message has no number, partner, CIF, issue date or upload date, so **any** filter naming one of those unpins it. Passing a row through a question it cannot answer would claim it matched. Only the Direcție checklist can speak for it — which is exactly what makes "eșuată" a value of that filter.
- **Delayed invoices (warning)**: every invoice has two dates — *data emiterii* (issue) and *încărcată în SPV* (upload/creation in the system). When the upload lands more than **5 working days** after issue (Mon–Fri; the e-Factura reporting deadline is five working days from issue), the row gets a 3px **amber** inset stripe and the **Încărcată** cell turns amber/600 — the highlight sits on the late fact itself, not on the issue date, now that both are visible. The count is working days in the interval *(issue, upload]*, so a Saturday issue date starts its clock on Monday by construction. Weekends are excluded; Romanian public holidays are **not** (a deliberate approximation — the flag is a soft warning, and a rare false amber costs less than a legal-holiday calendar that must track law changes). Amber (delayed) is visually distinct from red (failing). Sample delayed row: FF-88214 (emisă sâmbătă 11.07.2026, încărcată luni 20.07.2026 → 6 zile lucrătoare).
- Pills: 11px, 600 weight, 2px 8px padding, full radius. primită = accent-soft/accent; trimisă = mono-bg/muted + border; eșuată = red-bg/red.
- Selected row: `--sel` bg. Footer status line: "N afișate · 128 în arhivă" + "lista se încarcă pe măsură ce derulați" — **no pagination**; continuous scroll, lazy-load from SQLite.
- **The details pane auto-collapses.** With no selection there is nothing to show, so it folds to a 30px rail — left border, window bg, a faint `‹` chevron and a vertical "Detalii" label — and the table takes the width back, which is exactly when you want it: scanning. Selecting a row re-opens it; clicking the selected row again deselects and folds it; a `›` at the pane's top-right pins it shut even with a row selected — and it **stays** shut across later selections, because folding it is a preference about the layout, not a remark about one invoice. Reopening on the next selection would make the `›` a one-row undo and the state saved across launches would never once take effect; the rail's `‹` is the way back. Two widgets swapped by visibility, not a QSplitter — the pane is a fixed 250px reading column, not a draggable one. The window minimum stays 1160 whether the pane is open or folded, so collapsing then re-expanding can never land in a layout that does not fit.
- **Selection survives filtering unless the row itself is filtered out.** The current model clears it on every filter change (`_apply_filters` → `show_record(None)`), which throws away a pane the user is still reading and, with an auto-collapsing pane, makes the whole right side flicker on every keystroke in the search field. Drop the selection only when the selected `message_id` is no longer in the filtered result.
- Details pane 250px when open (window bg, left border): invoice number 15px/700, direction pill, key facts as label/value rows (Partener, De la CIF, Pentru CIF, Data emiterii, Încărcată în SPV, Total), "Fișiere pe disc" mono chips (.zip .pdf), archive path in a mono 10.5px box (mono-bg, word-break), buttons "Deschide PDF" (accent, primary) + "Arată în dosar" (outlined), then provenance under a top border, 11px faint labels: message_id, tip mesaj, arhivat la (values mono/tabular).
- Delayed selection additionally shows an amber panel (amber-bg, 1px amber border, 7px radius) above the key facts: bold "Declarată cu întârziere", then "Emisă 11.07.2026 · încărcată în SPV 20.07.2026 — după 6 zile lucrătoare (limita: 5 zile lucrătoare)".
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
- Delay highlight is conditional: delayed = working days in (issue date, upload date] > threshold (5 working days — the `health.DELAY_THRESHOLD_WORKING_DAYS` constant; promoting it to a config key stays parked until asked).
- Header sort: click a label to sort, click again to reverse; Direcție is inert. **Sorting must happen in SQL, not in a proxy.** The model pages through `fetchMore`, so a `QSortFilterProxyModel` would sort only the rows already fetched and silently re-order the list as the user scrolls. Give `Archive.catalog` an `order_by` / `descending` pair validated against a whitelist of sort keys (`issue_date`, `created_at`, `number`, `partner_name`, `from_cif`, `to_cif`, `total` — `direction` deliberately absent), keep the `issue_date IS NULL`-style nulls-last term, and **always append `message_id DESC` as a tiebreak** — without a unique final key, `LIMIT`/`OFFSET` paging over a non-unique sort key duplicates and skips rows between pages. Note `number` is TEXT, so its sort is lexicographic: "10" lands before "9", and invoice series make natural sort a rabbit hole not worth entering.
- Header filters: the ▽ opens that column's popover; filters apply live and combine with AND, and with search (which is `number LIKE ? OR partner_name LIKE ?`). The two new CIF filters belong in `Archive._catalog_filters` with the rest, as `LIKE` over the same `CASE direction …` expressions the columns render, so they stay SQL-side and paged. Keep them out of the `problems_only` branch, which is a client-side scan capped at `_SCAN_CAP` with `canFetchMore` disabled — anything routed through there inherits the cap. Seed the "CIF-urile tale" shortcuts from `Archive.distinct_cifs()`.
- Details pane collapse: driven by selection first (none → folded), overridable by the `›`/`‹` chevrons. Persist the user's pinned-shut state next to the geometry, like the header layout — but not the selection, which is per-session.
- Calendar range, CIF chips, radios, artifact cards, slider, select: all stateful as described.
- Column resize: dragging a header boundary re-proportions that column and Partener absorbs the difference; the boundary tracks the pointer in both directions. Widths survive the session and the next launch.
- Hovers: menu items → accent bg; table rows → hover bg; header boundaries → accent separator. No animations required; instant state changes are fine (desktop feel).
- Buttons "Deschide PDF"/"Arată în dosar" → open file / reveal in file manager. "Reîncearcă acum" → trigger a sync for that message.
- Template preview re-renders per keystroke; unknown `{var}` → error state, save should be blocked while invalid.
- Variable panel: the disclosure toggles on click and on Space/Enter; clicking a variable or a specifier example splices its text at the template field's caret (replacing any selection) and re-renders the preview on the same path as typing.

## State Management
Runtime state: selected message_id (null when nothing is selected — which folds the details pane); whether the pane is pinned shut; sort (column + direction); the open header popover, if any; one filter per filterable column — two date specs (mode + custom from/to), two text needles, two value checklists — plus the search needle and the "doar întârziate" flag; delay threshold (days); template text (+ validity); CIF list (free entry, min 1); direction; lookback_days; artifact set (min 1); frequency. Persisted via `SyncConfig` → `config.toml`. Catalog/failures read from `state.py`'s SQLite (`messages`, `failures` tables). Tray state = f(failures, auth): any failure → amber; auth expired/sync broken → red; else green.

## Sample Data
| Emisă | Încărcată în SPV | Număr | Partener | De la CIF | Pentru CIF | Direcție | Total | Stare |
|---|---|---|---|---|---|---|---|---|
| 11.07.2026 | — | — | TERMOENERGIA S.R.L. | — | — | eșuată | — | failing (red) |
| 18.07.2026 | 18.07.2026 | FCT-2107 | ELECTROMONTAJ CARPAȚI S.R.L. | 14338501 | 12345678 | primită | 4.821,50 RON | |
| 17.07.2026 | 17.07.2026 | 2026-0713 | DISTRIGAZ VEST S.A. | 11694562 | 87654321 | primită | 1.245,00 RON | |
| 15.07.2026 | 16.07.2026 | AS-1042 | MOBILA PRODEX S.R.L. | 12345678 | 22518743 | trimisă | 12.400,00 RON | |
| 11.07.2026 | 20.07.2026 | FF-88214 | BIROTICA PLUS S.R.L. | 31274865 | 12345678 | primită | 386,75 RON | delayed (amber) |
| 03.07.2026 | 06.07.2026 | FCT-1001 | ACME CONSTRUCT S.R.L. | 18293401 | 87654321 | primită | 2.480,00 RON | |

The sample spans both entries of the Setări CIF list (12345678, 87654321), and
**AS-1042 is the row that proves the rule**: it is the one `trimisă` invoice, so
the followed CIF sits in *De la* and the partner's in *Pentru* — the mirror of
every other row. The failing row knows **neither** side: nothing was downloaded, so there is no
partner CIF to read — and the `failures` table records no CIF of its own, so
not even the recipient is available. Both cells are em-dashes.

The table's "Emisă" column shows the issue date, "Încărcată" the SPV upload
date (this sample table's first two columns), newest issue date first. FF-88214
is the delayed sample under the working-day rule: issued Saturday 11.07, its
five-working-day window runs Monday 13.07 – Friday 17.07, and the Monday 20.07
upload is the sixth working day.

Romanian number format (1.234,56 RON), **Romanian dates everywhere: `zz.ll.aaaa`** ("18.07.2026" — never an abbreviated month, never ISO), correct diacritics everywhere. ISO stays internal: it is what the catalog stores and sorts by, and `{issue_date:%Y-%m-%d}` inside the path template is a *filename* convention, not a display format — do not conflate the two. Never translate code identifiers ({cif}, zip, pdf, config.toml, anafpy auth login).

## Suggested Qt mapping
QSystemTrayIcon + QMenu (QWidgetAction header/alert) · two independent top-level windows — QMainWindow for Facturi (1160×620 design size, width floor derived, no max), QDialog/QWidget for Setări (760×620 min, 1200×780 max) · custom QLayout subclass (flow-layout style) for the artifact cards, no QStackedWidget · QLineEdit (search) · QTableView + QAbstractTableModel ordering and filtering **in SQL**, no QSortFilterProxyModel (see Interactions) · custom QStyledItemDelegate for pills + red stripe · a **QHeaderView subclass** that owns the sort gesture outright: neither `setSortingEnabled` nor `sectionsClickable` is used, because both hand the click to `QHeaderView` — which flips its own sort indicator inside `mouseReleaseEvent` *before* it emits `sectionClicked`, so a handler reading the indicator there sees the already-flipped state and "click again to reverse" can never reverse. The header decides on the release instead, from the section captured on the press, and reports it through Qt's own `sortIndicatorChanged` — once per click. `restoreState` replays those interaction flags out of the saved blob, so they are re-asserted after every restore; `paintSection` draws both marks and `mousePressEvent` hit-tests the ▽ — QHeaderView hosts no child widgets, so the funnel is painted, not placed · each popover a frameless `Qt.Popup` QWidget (or a QMenu of QWidgetActions) anchored under its section · Interactive sections + one Stretch section · details pane QWidget/QFrame · QDateEdit + calendar popup (QCalendarWidget subclass for range) · QRadioButton, QSlider, QComboBox, QCheckBox cards · variable panel as a `QToolButton` (`ArrowType.Right/Down`) over a `QFrame` card, rows as flat focusable `QToolButton`s, `QLineEdit.insert()` for the caret splice · QSS themed from the token table (light/dark via two QSS sheets or QPalette).

## Out of scope — do not add
No login/credential UI (auth is the anafpy CLI), no delete/edit/upload, no charts/dashboards, no environment switcher, no onboarding, no pagination.

## Files
- `mockup/mockup.html` — the single clickable reference (open in a browser; `support.js` must sit alongside). Anchors: #1a tray, #1b Facturi, #1c Setări. A **light/dark picker sits at the top of the canvas** — mockup chrome, not product UI — so the file is reviewable in a plain browser and not only inside the design tool's Tweaks panel, which drives the same `theme` prop. The Tweaks panel also carries `templateError` (preview error state) and `variablePanel` (variable reference expanded/collapsed).
