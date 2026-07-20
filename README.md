# anaf-sync

<p>
  <a href="https://github.com/robert-malai/anaf-sync/actions/workflows/ci.yml"><img
    src="https://img.shields.io/github/actions/workflow/status/robert-malai/anaf-sync/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="https://codecov.io/gh/robert-malai/anaf-sync"><img
    src="https://img.shields.io/codecov/c/github/robert-malai/anaf-sync?branch=main" alt="Coverage"></a>
  <a href="https://pypi.org/project/anaf-sync/"><img
    src="https://img.shields.io/pypi/v/anaf-sync" alt="PyPI version"></a>
  <a href="https://pypi.org/project/anaf-sync/"><img
    src="https://img.shields.io/pypi/pyversions/anaf-sync" alt="Python versions"></a>
</p>

Scheduled local archiver for RO e-Factura invoices, built on
[anafpy](https://github.com/robert-malai/anafpy). Each run lists every message
in ANAF's 60-day retention window, downloads the ones it hasn't seen before,
and files them under paths rendered from a template of invoice variables.
Runs on Windows, Linux, and macOS.

Design rationale lives in [DESIGN.md](DESIGN.md); conventions for working on
the codebase in [CLAUDE.md](CLAUDE.md).

## Install

```bash
uv tool install anaf-sync        # from a published wheel
# or, from this checkout:
uv tool install --from . anaf-sync
```

## Authentication

anaf-sync reuses the anafpy login — the same one the anafpy MCP server uses:

1. `anafpy auth login` (browser OAuth with your ANAF certificate) writes the
   token set to the OS credential store (or a JSON file).
2. Set `ANAFPY_CLIENT_ID` and `ANAFPY_CLIENT_SECRET` in the environment (or a
   `.env` file) so scheduled runs can refresh expired tokens.

Optional, mirroring anafpy: `ANAFPY_TOKEN_STORE_BACKEND=file` and
`ANAFPY_TOKEN_STORE=~/.anafpy/tokens.json` for headless hosts without a
credential store.

## Configure

```bash
anaf-sync init            # writes a commented config.toml
anaf-sync status          # shows where it lives on your platform
```

The interesting part is the path template:

```toml
[output]
directory = "~/Facturi"
template  = "{cif}/{direction}/{issue_date:%Y}/{issue_date:%m}/{issue_date:%Y-%m-%d}_{number}_{partner_name}"
artifacts = ["zip", "pdf"]        # also: xml, signature, metadata
```

Templates use Python format syntax over the invoice's context: `number`,
`issue_date` / `due_date` (real dates — strftime specs work), `currency`,
`total`, `kind`, `direction`, `cif`, `seller_name`/`seller_cif`,
`buyer_name`/`buyer_cif`, `partner_name`/`partner_cif` (the *other* party),
`message_id`, `request_id`, `message_type`, `created`. Substituted values are
sanitised for the filesystem; literal `/` in the template creates folders; each
artifact appends its own extension.

## Run

```bash
anaf-sync sync --dry-run   # see what would be downloaded
anaf-sync sync             # download everything new
```

Runs are idempotent: a state file records archived message ids, so overlapping
60-day windows never duplicate work, and anything ANAF is about to purge has
already been captured.

## Schedule

```bash
anaf-sync schedule install --every 6h        # or --daily-at 07:30
anaf-sync schedule status
anaf-sync schedule remove
```

This registers the sync with the OS scheduler — Task Scheduler on Windows,
a systemd user timer on Linux (`loginctl enable-linger $USER` to run while
logged out), launchd on macOS. No daemon of its own.

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests && uv run black --check src tests && uv run mypy src
```
