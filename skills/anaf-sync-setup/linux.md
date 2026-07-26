# anaf-sync setup — Linux commands

Command blocks for [SKILL.md](SKILL.md), keyed by its step numbers. Use these
and only these on Linux. Linux is a first-class target here — a desktop or a
home/office server — but it is also what cloud sessions look like, so the
step-0 probe below comes first.

## Step 0 — real machine or cloud session?

```bash
systemd-detect-virt --container 2>/dev/null       # a container name = likely a cloud sandbox
systemctl --user is-system-running 2>/dev/null    # the scheduler's prerequisite
loginctl show-user "$USER" -p Linger 2>/dev/null
```

A per-user systemd instance (`running` or `degraded`) is the load-bearing
signal — the schedule in step 8 is a systemd **user** timer and cannot exist
without it. `is-system-running` failing outright, or a container verdict,
means a sandbox: stop, per SKILL.md step 0. When the signals conflict, ask
the user where this session is running.

Also note whether this is a **desktop or a headless server** (is there a
browser here?) — it changes step 5.

## Step 1 — probe block

```bash
command -v uv && uv --version
ls ~/.local/bin/anaf-sync ~/.local/bin/anafpy 2>/dev/null   # the uv tool installs
~/.local/bin/anaf-sync status 2>/dev/null                    # the one-stop probe
```

The config lives at `~/.config/anaf-sync/config.toml`, the `.env` for
scheduled runs right next to it — but always take the path from the
`config:` line of `status` rather than assuming.

If the `anafpy` CLI is present, probe the login now; on a fresh install,
probe **immediately after step 4**:

```bash
~/.local/bin/anafpy auth status
```

**Before asking the user for Client ID / Secret / CUI**, check what a
previous setup recorded:

```bash
cat ~/.config/anaf-sync/.env 2>/dev/null
```

Mention what you found and confirm the values are current before reusing
them. Only if nothing turns up do you send the user to the ANAF portal.

## Step 3 — install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

A freshly installed `uv` lands at `~/.local/bin/uv` — use that absolute path
for the rest of this session.

## Step 4 — install anaf-sync and the anafpy CLI

```bash
~/.local/bin/uv tool install anaf-sync    # or plain `uv` if the probe found it
~/.local/bin/uv tool install anafpy      # skip if ls found it in step 1
```

The binaries land next to `uv`: `~/.local/bin/anaf-sync` and
`~/.local/bin/anafpy`. **Immediately after installing, run the auth-status
probe** (step 1 above) — a surviving login makes step 5 unnecessary.

## Step 5 — login template (the user runs this)

On a **desktop** (browser on this machine):

```bash
~/.local/bin/anafpy auth login \
  --client-id <THEIR_CLIENT_ID> --client-secret <THEIR_CLIENT_SECRET> \
  --redirect-uri <THE_CALLBACK_THEY_REGISTERED>   # e.g. https://localhost:9002/callback
```

On a **headless server** (no browser, certificate on another machine), add
`--paste`: no local listener starts; the user opens the printed ANAF URL in a
browser on the machine that has the certificate, the browser ends on a
connection error at localhost — expected — and they copy that full URL from
the address bar back into the terminal within ~60 seconds.

Auth-status probe (you run this to verify):

```bash
~/.local/bin/anafpy auth status
```

**Step-5 env block** — the `.env` scheduled runs read (create the directory
if step 6 hasn't run yet; back up an existing file first, rule 2). Servers
without a desktop usually have **no credential store**, so the login above
would fail to save tokens — on those, include the two token-store lines
**before** the login runs, and export them in the login shell too:

```bash
mkdir -p ~/.config/anaf-sync
cat > ~/.config/anaf-sync/.env <<'EOF'
ANAFPY_CLIENT_ID=<THEIR_CLIENT_ID>
ANAFPY_CLIENT_SECRET=<THEIR_CLIENT_SECRET>
ANAFPY_TOKEN_STORE_BACKEND=file
ANAFPY_TOKEN_STORE=~/.anafpy/tokens.json
EOF
chmod 600 ~/.config/anaf-sync/.env
~/.local/bin/anaf-sync status | head -2      # must now say: auth: ... ok
```

Omit the two `ANAFPY_TOKEN_STORE*` lines on a desktop with a working keyring.

## Step 6 — configure

```bash
~/.local/bin/anaf-sync init <CIF>            # several CIFs: list them all
```

## Step 8 — schedule

```bash
~/.local/bin/anaf-sync schedule install --every 6h   # or --daily-at 07:30
~/.local/bin/anaf-sync schedule status
loginctl enable-linger "$USER"               # so the timer runs with no session open
```

The linger line matters on servers and desktops alike — without it the
`anaf-sync.timer` user unit only runs while the user is logged in. Deeper
probe when `schedule status` looks off:

```bash
systemctl --user list-timers anaf-sync.timer --all
systemctl --user status anaf-sync.service --no-pager -l | head -20
```

## Step 9 — tray app (desktops only)

```bash
~/.local/bin/uv tool install "anaf-sync[tray]"   # reinstalls with the GUI extra
~/.local/bin/anaf-sync tray install              # start at login (idempotent)
~/.local/bin/anaf-sync tray status
```

On GNOME the tray icon needs the *AppIndicator and KStatusNotifierItem
Support* extension; most other desktop environments show it out of the box.
`anaf-sync-tray` starts it right now without waiting for the next login.

## Logs block

Scheduled runs write to journald, identifier `anaf-sync` — no log files of
our own:

```bash
journalctl --user SYSLOG_IDENTIFIER=anaf-sync --since today
journalctl --user SYSLOG_IDENTIFIER=anaf-sync -p err     # errors only
```
