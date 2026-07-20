"""The ``anaf-sync`` command-line interface."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated

import structlog
import typer
from anafpy.exceptions import AnafError

from . import __version__
from .config import (
    AuthSettings,
    default_config_path,
    default_state_path,
    load_config,
    write_default_config,
)
from .engine import run_sync
from .scheduling import ScheduleError
from .scheduling import install as schedule_install
from .scheduling import status as schedule_status
from .scheduling import uninstall as schedule_uninstall
from .state import SyncState

app = typer.Typer(
    name="anaf-sync",
    help="Archive RO e-Factura invoices locally, on a schedule.",
    no_args_is_help=True,
)
schedule_app = typer.Typer(help="Manage the OS-level schedule for `anaf-sync sync`.")
app.add_typer(schedule_app, name="schedule")

_CONFIG_ENV = "ANAF_SYNC_CONFIG"

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help=f"Config file (default: ${_CONFIG_ENV} or the platform config dir).",
    ),
]


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _resolve_config_path(option: Path | None) -> Path:
    if option is not None:
        return option.expanduser()
    if env := os.environ.get(_CONFIG_ENV):
        return Path(env).expanduser()
    return default_config_path()


@app.callback()
def _main(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Debug logging.")
    ] = False,
) -> None:
    _configure_logging(verbose)


@app.command()
def version() -> None:
    """Print the anaf-sync version."""
    typer.echo(f"anaf-sync {__version__}")


@app.command()
def init(
    config: ConfigOption = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing config.")
    ] = False,
) -> None:
    """Write a commented default configuration file."""
    path = _resolve_config_path(config)
    try:
        write_default_config(path, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"wrote {path}")
    typer.echo("Edit it (CIF, output folder, template), then run: anaf-sync sync")


@app.command()
def sync(
    config: ConfigOption = None,
    days: Annotated[
        int | None,
        typer.Option(min=1, max=60, help="Override the lookback window."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List what would be downloaded; write nothing."),
    ] = False,
    redownload: Annotated[
        bool,
        typer.Option(
            "--redownload", help="Ignore the state file and fetch everything."
        ),
    ] = False,
) -> None:
    """Download all new e-Factura invoices into the archive."""
    config_path = _resolve_config_path(config)
    try:
        cfg = load_config(config_path)
        provider = AuthSettings.from_env().build_provider()
        state = SyncState.load(default_state_path())
        report = asyncio.run(
            run_sync(
                cfg,
                provider,
                state,
                days=days,
                dry_run=dry_run,
                redownload=redownload,
            )
        )
    except (AnafError, FileNotFoundError, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"listed {report.listed} | new {report.downloaded} | "
        f"already archived {report.already_archived} | "
        f"non-invoice {report.skipped_non_invoice}"
        + (f" | would download {report.would_download}" if dry_run else "")
    )
    if report.failures:
        for message_id, error in report.failures:
            typer.echo(f"failed {message_id}: {error}", err=True)
        raise typer.Exit(code=1)


@app.command()
def status(config: ConfigOption = None) -> None:
    """Show configuration, archive state, and schedule status."""
    config_path = _resolve_config_path(config)
    typer.echo(
        f"config:   {config_path} ({'ok' if config_path.exists() else 'MISSING'})"
    )
    state = SyncState.load(default_state_path())
    typer.echo(f"state:    {state.path} ({state.count} messages archived)")
    for message_id, failure in state.failures.items():
        typer.echo(
            f"failing:  {message_id} — {failure.attempts} run(s) since "
            f"{failure.first_failed_at:%Y-%m-%d}: {failure.error}"
        )
    if config_path.exists():
        cfg = load_config(config_path)
        typer.echo(f"cifs:     {', '.join(cfg.cifs)}  [{cfg.direction.value}]")
        typer.echo(f"output:   {cfg.output.resolved_directory}")
        typer.echo(f"template: {cfg.output.template}")
    typer.echo(f"schedule: {schedule_status()}")


@schedule_app.command("install")
def schedule_install_cmd(
    every: Annotated[
        str | None,
        typer.Option("--every", help="Run interval, e.g. 30m, 6h, 1d."),
    ] = None,
    daily_at: Annotated[
        str | None,
        typer.Option("--daily-at", help="Run once a day at HH:MM (24h)."),
    ] = None,
) -> None:
    """Register `anaf-sync sync` with the OS scheduler.

    Uses Task Scheduler on Windows, a systemd user timer on Linux, and
    launchd on macOS.
    """
    try:
        typer.echo(schedule_install(every=every, daily_at=daily_at))
    except ScheduleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@schedule_app.command("remove")
def schedule_remove_cmd() -> None:
    """Remove the scheduled job."""
    try:
        typer.echo(schedule_uninstall())
    except ScheduleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@schedule_app.command("status")
def schedule_status_cmd() -> None:
    """Show whether the scheduled job is installed."""
    typer.echo(schedule_status())


if __name__ == "__main__":
    app()
