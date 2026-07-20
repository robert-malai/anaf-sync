# Handoff: anaf-sync desktop companion UI (PySide6)

## Overview
UI for **anaf-sync**, a cross-platform tray tool that archives Romanian e-Factura invoices from ANAF to disk on a schedule. Three deliverables: a tray menu (3 status states), a main window "Facturi" view (invoice catalog with details pane), and a "Setări" view (config editor over `config.toml`). Read-only over the archive; its core job is making silent failures visible before ANAF's 60-day purge.

## About the Design Files
`mockup/anaf-sync Mockup.dc.html` (open in a browser, keep `support.js` next to it) is a **design reference built in HTML** — a clickable prototype showing intended look and behavior. It is NOT production code. The task is to **recreate it in PySide6** using Qt idioms and the existing `anaf_sync` package (`config.py`, `state.py`) as the data layer. On-screen anchors: `#1a` tray menu, `#1b` Facturi, `#1c` Setări.

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

### 2. Main window — Facturi (980×620)
- Titlebar 38px, panel bg, centered muted title "anaf-sync".
- Sidebar 148px, window bg, right border: "Facturi" (active: accent-soft bg, accent text, 600 weight, badge 128 accent pill) and "Setări" (muted, hover row-hover). → `QStackedWidget` for the two views.
- Toolbar row: search field (window bg, 1px border, 6px radius, placeholder "Caută după număr sau partener…") + filter chips **Toate / Primite / Trimise / Probleme (1)** (active: accent bg + on-accent; inactive: window bg, border, muted).
- Period row (wraps): label "Perioadă" + chips **Luna curentă / Toate / Personalizat…** (active: accent-soft bg + accent border/text). "Personalizat…" reveals two 112px date fields (din–până); focusing either opens a **QCalendarWidget-style popup**: tinted nav bar (◀ "iulie 2026 ▼" ▶ in accent), weekday header lun.–dum. with weekends red, grayed out-of-month days, 30×24 cells. Range selection: first click = start, second = end (auto-close; clicks swapped if reversed); endpoints accent bg, in-between accent-soft. Qt: two `QDateEdit` + popup calendar, or a custom range calendar.
- Table header: 11px, faint, uppercase, letter-spacing .04em. Columns: Data 52px / Număr 88px / Partener flex / Direcție 76px / Total 96px right-aligned, gap 8px, row padding 9px 14px, 1px bottom borders. → `QTableView` + model over `state.py`'s `messages` table, `QSortFilterProxyModel` for chips/period/search.
- **Failing row pinned on top**: 3px red inset stripe on the left edge, red text, "eșuată" pill (red-bg/red), em-dashes for număr/total.
- **Delayed invoices (warning)**: every invoice has two dates — *data emiterii* (issue) and *încărcată în SPV* (upload/creation in the system). When upload − issue > **5 days** (configurable threshold), the row gets a 3px **amber** inset stripe and the Data cell turns amber/600. Amber (delayed) is visually distinct from red (failing). Sample delayed row: FF-88214 (emisă 11 iul., încărcată 19 iul. → 8 zile).
- Pills: 11px, 600 weight, 2px 8px padding, full radius. primită = accent-soft/accent; trimisă = mono-bg/muted + border; eșuată = red-bg/red.
- Selected row: `--sel` bg. Footer status line: "N afișate · 128 în arhivă" + "lista se încarcă pe măsură ce derulați" — **no pagination**; continuous scroll, lazy-load from SQLite.
- Details pane 250px (window bg, left border): invoice number 15px/700, direction pill, key facts as label/value rows (Partener, CIF partener, Data emiterii, Încărcată în SPV, Total), "Fișiere pe disc" mono chips (.zip .pdf), archive path in a mono 10.5px box (mono-bg, word-break), buttons "Deschide PDF" (accent, primary) + "Arată în dosar" (outlined), then provenance under a top border, 11px faint labels: message_id, tip mesaj, arhivat la (values mono/tabular).
- Delayed selection additionally shows an amber panel (amber-bg, 1px amber border, 7px radius) above the key facts: bold "Declarată cu întârziere", then "Emisă 11 iul. · încărcată în SPV 19 iul. — după 8 zile (limita: 5 zile)".
- Failing selection instead: partner name 15px/700, "eșuată" pill, red panel (red-bg, 1px red border): bold "Descărcarea eșuează repetat", then "Eșuează din **11 iul.** · **6 încercări** / Ultima eroare: `HTTP 500` / Expiră din SPV în **9 zile**"; red "Reîncearcă acum" button; provenance: message_id 3210447810, tip FACTURA PRIMITA.

### 3. Main window — Setări
Same shell, sidebar "Setări" active. Scrollable form, three sections with uppercase 11px faint headers, separated by 1px rules; 150px label column. Every control maps to a `SyncConfig` key — no invented options.

**Companie**
- *CIF-uri urmărite* → `cifs`: **multiselect chips over a fixed list** (the available CIFs come from the ANAF login, no free add). Chips: mono, checkbox square inside (accent when checked), accent-soft bg + accent border when selected. Sample: 12345678 ✓, 87654321 ✓, 40118293 unchecked. Help: "Lista provine din autentificarea ANAF — cel puțin un CIF rămâne selectat." The last selected CIF cannot be unchecked (`cifs` min_length=1).
- *Direcție* → `direction`: radios Primite / Trimise / Ambele (received/sent/both), default Primite.
- *Fereastră de căutare* → `lookback_days`: slider 1–60, value label "60 zile", help "ANAF păstrează mesajele cel mult 60 de zile."

**Arhivă**
- *Dosar arhivă* → `output.directory`: mono read-only field "~/Facturi" + "Alege…" (`QFileDialog.getExistingDirectory`).
- *Șablon de denumire* → `output.template`: mono editable field, default `{cif}/{direction}/{issue_date:%Y}/{issue_date:%m}/{issue_date:%Y-%m-%d}_{number}_{partner_name}`. **Live preview** below on every keystroke, rendered against a sample invoice:
  - valid → green box: `Previzualizare: ~/Facturi/12345678/received/2026/07/2026-07-03_FCT-1001_ACME CONSTRUCT S.R.L..zip`
  - unknown variable → red box: `Variabilă necunoscută: {numer}`
  Valid variables (from `template.py`/config docs): number, issue_date, due_date, currency, total, kind, direction, cif, seller_name, seller_cif, buyer_name, buyer_cif, partner_name, partner_cif, message_id, request_id, message_type, created. Dates take strftime specs.
- *Fișiere salvate* → `output.artifacts`: checkbox cards, 3-col grid (max 560px). Names stay in English mono; descriptions Romanian 11px faint: **zip** "arhiva semnată originală" ✓, **pdf** "redarea oficială ANAF" ✓, **xml** "XML-ul UBL al facturii", **signature** "semnătura MF detașată", **metadata** "fișier JSON cu detaliile mesajului". Checked card: accent border + accent-soft bg.

**Programare**
- *Frecvență*: select — La fiecare oră / 3 ore / **6 ore** (default) / 12 ore / O dată pe zi.
- Status line: green dot + green 12.5px "Activă — următoarea rulare: marți 21 iul., 06:00".

**Footer save bar** (pinned, window bg, top border): note "Modificările se scriu în `config.toml` — fișierul rămâne editabil manual" + "Renunță" (outlined) / "Salvează modificările" (accent primary).

## Interactions & Behavior
- Table row click → selects (sel bg) and swaps details pane content; failing row swaps to the red panel; delayed rows add the amber panel.
- Delay highlight is conditional: delayed = (upload date − issue date) > threshold (default 5 days; make it a constant or config-derived).
- Filter chips, period chips, calendar range, CIF chips, radios, artifact cards, slider, select: all stateful as described; filters combine (direction ∧ period).
- Hovers: menu items → accent bg; sidebar/table rows → hover bg. No animations required; instant state changes are fine (desktop feel).
- Buttons "Deschide PDF"/"Arată în dosar" → open file / reveal in file manager. "Reîncearcă acum" → trigger a sync for that message.
- Template preview re-renders per keystroke; unknown `{var}` → error state, save should be blocked while invalid.

## State Management
Runtime state: selected message_id; filter chip; period (+ custom from/to); delay threshold (days); template text (+ validity); CIF selection set (min 1); direction; lookback_days; artifact set (min 1); frequency. Persisted via `SyncConfig` → `config.toml`. Catalog/failures read from `state.py`'s SQLite (`messages`, `failures` tables). Tray state = f(failures, auth): any failure → amber; auth expired/sync broken → red; else green.

## Sample Data
| Emisă | Încărcată în SPV | Număr | Partener | Direcție | Total | Stare |
|---|---|---|---|---|---|---|
| 11 iul. | — | — | TERMOENERGIA S.R.L. | eșuată | — | failing (red) |
| 18 iul. | 18 iul. | FCT-2107 | ELECTROMONTAJ CARPAȚI S.R.L. | primită | 4.821,50 RON | |
| 17 iul. | 17 iul. | 2026-0713 | DISTRIGAZ VEST S.A. | primită | 1.245,00 RON | |
| 15 iul. | 16 iul. | AS-1042 | MOBILA PRODEX S.R.L. | trimisă | 12.400,00 RON | |
| 11 iul. | 19 iul. | FF-88214 | BIROTICA PLUS S.R.L. | primită | 386,75 RON | delayed (amber) |
| 3 iul. | 6 iul. | FCT-1001 | ACME CONSTRUCT S.R.L. | primită | 2.480,00 RON | |

Table "Data" column shows the issue date.

Romanian number format (1.234,56 RON), day-month dates ("18 iul."), correct diacritics everywhere. Never translate code identifiers ({cif}, zip, pdf, config.toml, anafpy auth login).

## Suggested Qt mapping
QSystemTrayIcon + QMenu (QWidgetAction header/alert) · QMainWindow fixed 980×620 · sidebar QListWidget/custom + QStackedWidget · QLineEdit (search) · QToolButton checkable chips · QTableView + QAbstractTableModel + QSortFilterProxyModel · custom QStyledItemDelegate for pills + red stripe · details pane QWidget/QFrame · QDateEdit + calendar popup (QCalendarWidget subclass for range) · QRadioButton, QSlider, QComboBox, QCheckBox cards · QSS themed from the token table (light/dark via two QSS sheets or QPalette).

## Out of scope — do not add
No login/credential UI (auth is the anafpy CLI), no delete/edit/upload, no charts/dashboards, no environment switcher, no onboarding, no pagination.

## Files
- `mockup/anaf-sync Mockup.dc.html` — the clickable reference (open in a browser; `support.js` must sit alongside). Anchors: #1a tray, #1b Facturi, #1c Setări. A light/dark switch is available in the design tool's Tweaks panel; token table above covers both.
