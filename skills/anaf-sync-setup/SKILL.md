---
name: anaf-sync-setup
description: >
  Install, configure, verify, or repair anaf-sync on this computer — the
  scheduled local archiver that downloads RO e-Factura invoices from ANAF
  before the 60-day retention window deletes them, and files them on disk
  under human-readable paths. Use when the user wants to set up / install /
  configure anaf-sync, archive their e-Factura invoices automatically, or
  when an existing setup misbehaves ("the sync stopped working", "the tray
  icon is red/yellow", "invoices aren't being downloaded anymore"). Probes
  what is already in place, installs only what is missing, and ends with an
  unattended schedule running. Safe to re-run at any time.
---

# Set up anaf-sync on this computer

You are installing an invoice archiver on someone's computer. Assume the user
is an accountant or business owner, not a programmer: never show them a stack
trace, never ask them to edit TOML by hand, and explain each step in one plain
sentence before you run it. The user is Romanian by construction (RO e-Factura
only serves Romanian fiscal entities) — speak whichever language they speak,
but keep CLI flags, env vars, and template variables verbatim.

The [anaf-sync README](https://github.com/robert-malai/anaf-sync) is the full
human-readable guide (in Romanian) — this skill is the automated version of
it. When the two disagree, the README is right.

This skill is a **diagnostic, not a script**. Probe first, report what is
already done, then do only what is missing. A first-time install and a
six-months-later repair are the same flow.

This file is the **spine**: what each step is for, the rules, and what to tell
the user. The exact commands live in the platform files — [macos.md](macos.md),
[windows.md](windows.md), and [linux.md](linux.md). Step 0 tells you which one
applies; read it once, in full, and take **every command from that file only**.
Never run a command block written for another platform.

## Rules that override everything else

1. **Never ask for, accept, or echo the certificate PIN.** The USB token's PIN
   and 2FA are between the user and their device. If a step needs the PIN, the
   *user* runs that command and tells you the outcome.
2. **Never overwrite a config without backing it up first.** Before
   `anaf-sync init --force` or editing `config.toml`, copy the existing file
   aside (`config.toml.bak-<something>`).
3. **Verify, never assume.** After each step, run the probe that proves it
   worked. "The user said they did it" is not evidence.
4. **Never move, rename, or delete anything inside an invoice archive.** The
   archive folder is the user's durable fiscal record — ANAF will not return
   an invoice after 60 days. `backfill` is read-only by design; so are you.

## Step 0 — Preflight: is this the right machine?

The end state of this skill is a **scheduled job on this machine**. That only
makes sense on the computer that will hold the archive and is switched on
regularly — not in a cloud or remote session, where the schedule would
evaporate with the sandbox.

Run `uname -s`:

- **macOS** (`Darwin`) → read [macos.md](macos.md) now.
- **Windows** → read [windows.md](windows.md) now.
- **`Linux`** → two legitimate possibilities, and one trap. A Linux desktop or
  a home/office server the user owns is a first-class target — read
  [linux.md](linux.md); its probe block tells a real machine apart from a
  cloud session (a per-user systemd instance is the load-bearing signal — the
  scheduler needs it). If the probes say container/no-systemd, or the user
  confirms they are in a cloud session: **stop** and tell them: *"This session
  is running on a temporary machine, not on your computer, so anything I
  schedule here would disappear. Start a new session in the Code tab and pick
  the **Local** environment (or SSH to the server that should keep the
  archive), then run this again."*

When in doubt, ask the user directly where they want the invoices to live —
the machine holding that folder is the machine this skill must run on.

## Step 1 — Probe everything, then report

Run the platform file's **step-1 probe block** and build a picture before
touching anything. You are establishing:

- is `uv` installed?
- are `anaf-sync` **and** the `anafpy` CLI installed as uv tools? (Two
  installs: anaf-sync brings the archiver, but the login/refresh CLI is
  anafpy's own — `uv tool install anaf-sync` does not expose it.)
- what does `anaf-sync status` say? This one command answers most of the
  checklist at once: the config path (and whether the file exists), whether
  `ANAFPY_CLIENT_ID`/`SECRET` are found, the archive state and last run,
  currently-failing messages, and the schedule status.
- is the user logged in to ANAF (`anafpy auth status`)? **This is the big
  reuse win**: anaf-sync deliberately shares anafpy's auth, so a user who
  already set up the anafpy MCP connector (or any prior anafpy login) skips
  the whole certificate ceremony — steps 2 and 5 vanish.

Then give the user a short checklist of what is done and what is missing —
six lines, not a wall of text. Something like:

> - ANAF application registered: **I need to ask you**
> - uv installed: **yes**
> - anaf-sync installed: **yes** (0.3.0)
> - Logged in to ANAF: **yes** — reusing your existing anafpy login
> - Configured: **no** — no config.toml yet
> - Scheduled: **no**
>
> So we need to write the configuration, do a first sync, and schedule it.

Then do only the missing steps.

## Step 2 — ANAF application (user-only, you cannot do this)

Registering the OAuth application happens on ANAF's portal with their
certificate. You cannot drive it. **Before sending anyone to the portal**,
check for existing credentials: the step-1 probe may already show `auth: ok`;
the platform file lists where a previous anafpy or anaf-sync setup may have
recorded the Client ID/Secret. An existing anafpy OAuth profile is fully
reusable — never ask the user to register a second one.

If they truly have no **Client ID** and **Client Secret**, point them at
[step 1 of the anafpy setup guide](https://anafpy.readthedocs.io/en/latest/mcp/setup/#step-1-register-an-oauth-application-on-anafs-portal)
and summarize it in three lines: enroll as an API user, create a *Profil
Oauth* with callback `https://localhost:9002/callback` (the `https://`
matters — the portal rejects `http://`), tick **E-Factura** (the only service
anaf-sync needs), press *Generare Client ID*. Note down which callback URL
they registered — the login in step 5 must use exactly that one.

Ask for the Client ID, the Client Secret, and the firm's **CUI** when you need
them — not before. **Before you ask for the secret, tell them plainly**:
*"The Client Secret will be visible in this conversation and saved in a
settings file on this computer. That's normal for this setup — it's how the
archiver authenticates to ANAF — but don't paste it anywhere else."* Say this
once, then ask.

## Step 3 — uv

Only needed if the probe said it is missing. Install it with the platform
file's **step-3 block**. `uv` brings its own Python; do not install Python
separately. A freshly installed `uv` will not be on this session's `PATH` —
use the absolute path the platform file gives, rather than telling the user
to restart anything.

## Step 4 — Install anaf-sync (and the anafpy CLI)

Both are on PyPI; install them as uv tools with the platform file's **step-4
block** — `anaf-sync` (the archiver) and `anafpy` (the auth CLI it relies
on). To update later: `uv tool upgrade anaf-sync` / `uv tool upgrade anafpy`.

If the probe found `anafpy` already installed (e.g. from the anafpy MCP
setup), leave it alone — it is the same CLI.

**Immediately after installing, probe `anafpy auth status`** — a login from a
previous setup may still be valid, making step 5 unnecessary.

## Step 5 — Log in to ANAF (the user runs this)

Skip this entirely when `anafpy auth status` already reports an authenticated
session. Otherwise: this step needs their browser, their certificate, and
possibly their PIN. **You cannot drive it.** Compose the command from the
platform file's **step-5 template** — the `--redirect-uri` must be the exact
callback registered on their OAuth profile — and ask them to run it in the
integrated terminal.

The choreography, condensed (the authoritative walkthrough is
[step 4 of the anafpy setup guide](https://anafpy.readthedocs.io/en/latest/mcp/setup/#step-4-log-in-to-anaf-one-time-with-your-certificate)
— read it if anything surprises you):

- the browser opens ANAF's login page and asks for the certificate;
- then a **"connection is not private" warning at localhost — expected**
  (a one-time certificate so their own computer can catch ANAF's answer);
  they click Advanced → Proceed to localhost;
- fallback: if the terminal says it is waiting for a pasted URL, they copy
  the full URL from the browser's address bar into the terminal within ~60
  seconds.

Verify with the platform file's **auth-status probe**. Tokens refresh
automatically for about a year; this ceremony recurs roughly annually.

**Then the step that makes scheduled runs survive — do not skip it.**
Interactive shells see `ANAFPY_CLIENT_ID`/`SECRET` from the environment, but
scheduled runs (Task Scheduler, launchd, systemd) start from an undefined
directory and read no shell profile. anaf-sync's answer is a `.env` **next to
`config.toml`** in the config directory — the one place scheduled runs always
look. Write it with the platform file's **step-5 env block** (create the
directory if this runs before step 6), then verify `anaf-sync status` reports
`auth: ANAFPY_CLIENT_ID/SECRET ok`. Without this file, the schedule works
until the first token refresh and then silently starts failing.

On a headless Linux box there is one more line for that `.env` — the file
token store; [linux.md](linux.md) has it.

## Step 6 — Configure (the interview)

Generate the config with the platform file's **step-6 block**:
`anaf-sync init <CIF>` — the CIF is required (several CIFs for several
firms; the `RO` prefix is stripped automatically). The file lands at the
config path `anaf-sync status` prints, fully commented.

Then the part that genuinely needs you: translating how the user wants their
invoices organized into the `[output]` section. Interview, don't lecture —
three questions:

1. **Where?** → `directory` (default `~/Facturi` is fine for most).
2. **How should the folders and file names look?** → `template`. It is
   Python `str.format` over the invoice's data. The variables:
   `number`, `issue_date` / `due_date` (real dates — `strftime` specifiers
   work), `issue_month` / `created_month` (Romanian month names, lowercase),
   `currency`, `kind`, `direction`, `cif`, `partner_name` / `partner_cif`
   (the *other* party, whatever the direction), `message_id`, `request_id`,
   `message_type`, `created`. Capitalization conversions: `{issue_month!u}`
   → `IULIE`, `!c` → `Iulie`, `!l` → `iulie`, `{partner_name!t}` → `Furnizor
   Srl`. A literal `/` creates folders. Worked translations:
   - *"a folder per month, supplier name in the file name"* →
     `{issue_date:%Y}/{issue_date:%m}-{issue_month}/{issue_date:%Y-%m-%d}_{number}_{partner_name}`
   - *"received and sent apart, then by year"* →
     `{direction}/{issue_date:%Y}/{issue_date:%Y-%m-%d}_{number}_{partner_name}`
   - *"several firms, each its own tree"* → start the template with `{cif}/`.
3. **What gets saved?** → `artifacts`, from: `zip`, `xml`, `signature`,
   `pdf`, `metadata`.

Two opinions you state rather than ask:

- **Keep `zip` in `artifacts`.** It is the signed original ANAF hands over;
  the XML, signature, and PDF all derive from it, and nothing can be
  reconstructed the other way once ANAF's 60-day window shuts. The PDF is
  what they read; the ZIP is what they keep.
- **Warn about templates built only from invoice-XML variables** (`number`,
  `issue_date`, `partner_name`, ...). Messages without an XML invoice (error
  files, buyer messages) render those as `unknown` and would pile up on one
  path — keep `message_id` or `direction` somewhere in the template, as the
  examples above do.

Edit `config.toml` yourself (rule 2: back it up first) — preserve its
comments; the user may edit it by hand later. Read the file back after
writing to confirm the TOML parses (`anaf-sync status` reports
`config: INVALID` on a broken file).

## Step 7 — First sync

```bash
anaf-sync sync --dry-run
```

Show the user what would be downloaded and where the first few paths would
land — this is their chance to adjust the template while it costs nothing.
Then run the real `anaf-sync sync` and report the counts (`listed`, `new`,
`failures`) in plain words. A few failures are not alarming: a message that
could not be written is retried automatically on every following run while
ANAF still has it; `anaf-sync status` lists each one with its
days-until-purge countdown.

Runs are idempotent — a state database remembers what was already archived,
so overlapping 60-day windows never duplicate anything. `--redownload` exists
for after a template change; `--days N` narrows one run's window.

## Step 8 — Schedule (the point of all this)

Ask how often they want it to run — suggest **every 6 hours** (`--every 6h`);
once a day (`--daily-at 07:30`) is also perfectly safe against a 60-day
window. Then, from the platform file's **step-8 block**:

```bash
anaf-sync schedule install --every 6h
anaf-sync schedule status
```

This registers the sync with the OS's own scheduler — Task Scheduler on
Windows, launchd on macOS, a systemd user timer on Linux (which needs the
linger setting from [linux.md](linux.md) to run without an open session). No
daemon of our own. Verify with `schedule status`, and check the platform
file's deeper probe if it looks off.

Close by telling them what they now have: *"From now on this runs by itself —
new invoices appear in your folder within hours of reaching ANAF. You don't
need to do anything until the yearly ANAF login expires; if the archive ever
looks stale, run me again and I'll diagnose it."*

## Step 9 (optional) — Backfill and the tray app

**Backfill** — only if they have older invoice ZIPs on disk (downloaded
manually from SPV, by their accountant, or by a previous install):

```bash
anaf-sync backfill <folder> --dry-run   # show what it would catalog
anaf-sync backfill <folder>
```

Say what it is before running: **read-only** — it catalogs existing ZIPs into
the archive's index without downloading, moving, or renaming anything, and
running it twice updates rather than duplicates. It is also the recovery path
if the state database is ever lost (run it over the output directory).

**Tray app** — a status icon showing archive health at a glance: **green** =
up to date, **yellow** = needs attention (an invoice failing repeatedly, or
one declared late), **red** = sync broken (usually the yearly ANAF login
expired → step 5). Install with the platform file's **step-9 block** (adds
the PySide6 `tray` extra, then `anaf-sync tray install` for start-at-login),
or point them at the prebuilt bundles on the
[Releases page](https://github.com/robert-malai/anaf-sync/releases/latest) —
those need no Python, but are not code-signed yet: on first launch macOS
wants right-click → *Open* once, Windows *More info* → *Run anyway*.

## When something fails

`anaf-sync status` first — it names the broken piece (missing credentials,
invalid config, failing messages with their purge countdown, schedule state).
For a scheduled run that misbehaved, read the platform's native log with the
platform file's **logs block**; there are no log files of our own.

Three failures are **not** installation problems — say so plainly instead of
fixing:

- **A message keeps failing to download or render** — often ANAF's side (e.g.
  its PDF renderer refusing a document). It retries on every run while the
  60-day window is open; only act if the countdown gets short.
- **ANAF's service is down or flaky** — the next scheduled run picks up
  whatever this one missed; nothing here is broken.
- **The red tray dot after ~a year** — the ANAF login expired on schedule.
  Re-run step 5, nothing else.
