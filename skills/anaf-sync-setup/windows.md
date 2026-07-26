# anaf-sync setup — Windows commands

Command blocks for [SKILL.md](SKILL.md), keyed by its step numbers. Use these
and only these on Windows. Blocks you run yourself are written for your bash
shell (the Code tab runs Git Bash on Windows); commands the **user** runs are
given in PowerShell form, since that is what their terminal will be.

One non-concern worth knowing: the `ANAFPY_CURL` workaround from the anafpy
MCP setup does **not** apply here — anaf-sync never drives the
certificate-based logins that need it. The only login in this flow is the
browser OAuth one.

## Step 1 — probe block

```bash
command -v uv && uv --version
ls "$USERPROFILE/.local/bin/anaf-sync.exe" "$USERPROFILE/.local/bin/anafpy.exe" 2>/dev/null
"$USERPROFILE/.local/bin/anaf-sync.exe" status 2>/dev/null    # the one-stop probe
```

The config lives at `%LOCALAPPDATA%\anaf-sync\config.toml`, the `.env` for
scheduled runs right next to it — but always take the path from the
`config:` line of `status` rather than assuming.

**Login state outlives the binaries.** Tokens live in Windows Credential
Manager, not next to any install. If the `anafpy` CLI is present, probe now;
on a fresh install, probe **immediately after step 4**:

```bash
"$USERPROFILE/.local/bin/anafpy.exe" auth status
```

**Before asking the user for Client ID / Secret / CUI**, check what previous
setups recorded:

```bash
cat "$LOCALAPPDATA/anaf-sync/.env" 2>/dev/null
grep -h '"ANAFPY_' "$APPDATA/Claude/claude_desktop_config.json"* 2>/dev/null
```

(The second line covers a machine that already runs the anafpy MCP connector,
including that skill's config backups.) Mention what you found and confirm
the values are current before reusing them. Only if nothing turns up do you
send the user to the ANAF portal.

## Step 3 — install uv

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

A freshly installed `uv` lands at `$USERPROFILE/.local/bin/uv.exe` — use that
absolute path for the rest of this session.

## Step 4 — install anaf-sync and the anafpy CLI

```bash
"$USERPROFILE/.local/bin/uv.exe" tool install anaf-sync   # or plain `uv` if the probe found it
"$USERPROFILE/.local/bin/uv.exe" tool install anafpy      # skip if ls found it in step 1
```

The binaries land next to `uv`: `%USERPROFILE%\.local\bin\anaf-sync.exe` and
`%USERPROFILE%\.local\bin\anafpy.exe`. **Immediately after installing, run
the auth-status probe** (step 1 above) — a surviving login makes step 5
unnecessary.

## Step 5 — login template (the user runs this, PowerShell)

```powershell
& "$env:USERPROFILE\.local\bin\anafpy.exe" auth login --client-id <THEIR_CLIENT_ID> --client-secret <THEIR_CLIENT_SECRET> --redirect-uri <THE_CALLBACK_THEY_REGISTERED>
```

(`<THE_CALLBACK_THEY_REGISTERED>` is e.g. `https://localhost:9002/callback`.)
Auth-status probe (you run this to verify):

```bash
"$USERPROFILE/.local/bin/anafpy.exe" auth status
```

**Step-5 env block** — the `.env` scheduled runs read (create the directory
if step 6 hasn't run yet; back up an existing file first, rule 2):

```bash
mkdir -p "$LOCALAPPDATA/anaf-sync"
cat > "$LOCALAPPDATA/anaf-sync/.env" <<'EOF'
ANAFPY_CLIENT_ID=<THEIR_CLIENT_ID>
ANAFPY_CLIENT_SECRET=<THEIR_CLIENT_SECRET>
EOF
"$USERPROFILE/.local/bin/anaf-sync.exe" status | head -2   # must now say: auth: ... ok
```

## Step 6 — configure

```bash
"$USERPROFILE/.local/bin/anaf-sync.exe" init <CIF>          # several CIFs: list them all
```

## Step 8 — schedule

```bash
"$USERPROFILE/.local/bin/anaf-sync.exe" schedule install --every 6h   # or --daily-at 07:30
"$USERPROFILE/.local/bin/anaf-sync.exe" schedule status
```

This registers a Task Scheduler task named `AnafSync`. Deeper probe when
`schedule status` looks off:

```bash
schtasks //Query //TN AnafSync //V //FO LIST    # Git Bash doubles the slashes
```

## Step 9 — tray app

```bash
"$USERPROFILE/.local/bin/uv.exe" tool install "anaf-sync[tray]"   # reinstalls with the GUI extra
"$USERPROFILE/.local/bin/anaf-sync.exe" tray install              # start at login (idempotent)
"$USERPROFILE/.local/bin/anaf-sync.exe" tray status
```

`anaf-sync-tray.exe` (next to the other binaries) starts it right now
without waiting for the next login.

## Logs block

Scheduled runs write to the Application event log, source `anaf-sync` — no
log files of our own:

```powershell
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='anaf-sync'} -MaxEvents 20
```
