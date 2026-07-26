# anaf-sync setup — macOS commands

Command blocks for [SKILL.md](SKILL.md), keyed by its step numbers. Use these
and only these on macOS.

## Step 1 — probe block

```bash
command -v uv && uv --version
ls ~/.local/bin/anaf-sync ~/.local/bin/anafpy 2>/dev/null   # the uv tool installs
~/.local/bin/anaf-sync status 2>/dev/null                    # the one-stop probe
```

The config lives at `~/Library/Application Support/anaf-sync/config.toml`,
the `.env` for scheduled runs right next to it — but always take the path
from the `config:` line of `status` rather than assuming.

**Login state outlives the binaries.** Tokens live in the macOS Keychain
(service `anafpy`), not next to any install — "anaf-sync is not installed"
does NOT mean "not logged in". If the `anafpy` CLI is present, probe now; on
a fresh install, probe **immediately after step 4**:

```bash
~/.local/bin/anafpy auth status
```

The Keychain entry holds **tokens only — never the Client ID or Secret**.
**Before asking the user for Client ID / Secret / CUI**, check what previous
setups recorded:

```bash
cat ~/Library/Application\ Support/anaf-sync/.env 2>/dev/null
grep -h '"ANAFPY_' ~/Library/Application\ Support/Claude/claude_desktop_config.json* 2>/dev/null
```

(The second line covers a machine that already runs the anafpy MCP connector,
including that skill's config backups.) Mention what you found and confirm
the values are current before reusing them. Only if nothing turns up do you
send the user to the ANAF portal.

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
probe** (step 1 above) — a surviving Keychain login makes step 5 unnecessary.

## Step 5 — login template (the user runs this)

```bash
~/.local/bin/anafpy auth login \
  --client-id <THEIR_CLIENT_ID> --client-secret <THEIR_CLIENT_SECRET> \
  --redirect-uri <THE_CALLBACK_THEY_REGISTERED>   # e.g. https://localhost:9002/callback
```

Auth-status probe (you run this to verify):

```bash
~/.local/bin/anafpy auth status
```

**Step-5 env block** — the `.env` scheduled runs read (create the directory
if step 6 hasn't run yet; back up an existing file first, rule 2):

```bash
mkdir -p ~/Library/Application\ Support/anaf-sync
cat > ~/Library/Application\ Support/anaf-sync/.env <<'EOF'
ANAFPY_CLIENT_ID=<THEIR_CLIENT_ID>
ANAFPY_CLIENT_SECRET=<THEIR_CLIENT_SECRET>
EOF
chmod 600 ~/Library/Application\ Support/anaf-sync/.env
~/.local/bin/anaf-sync status | head -2      # must now say: auth: ... ok
```

## Step 6 — configure

```bash
~/.local/bin/anaf-sync init <CIF>            # several CIFs: list them all
```

## Step 8 — schedule

```bash
~/.local/bin/anaf-sync schedule install --every 6h   # or --daily-at 07:30
~/.local/bin/anaf-sync schedule status
```

This writes a launchd agent, label `ro.anaf-sync.sync`, at
`~/Library/LaunchAgents/ro.anaf-sync.sync.plist`. Deeper probe when
`schedule status` looks off:

```bash
launchctl list ro.anaf-sync.sync
```

## Step 9 — tray app

```bash
~/.local/bin/uv tool install "anaf-sync[tray]"   # reinstalls with the GUI extra
~/.local/bin/anaf-sync tray install              # start at login (idempotent)
~/.local/bin/anaf-sync tray status
```

`anaf-sync-tray` (next to the other binaries) starts it right now without
waiting for the next login.

## Logs block

Scheduled runs write to the unified log, subsystem `ro.anaf-sync` — no log
files of our own:

```bash
log show --last 1d --info --predicate 'subsystem == "ro.anaf-sync"'
log stream --predicate 'subsystem == "ro.anaf-sync"'    # live, during a sync
```
