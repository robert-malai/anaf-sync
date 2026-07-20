"""The ``anaf-sync`` command-line interface."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from types import TracebackType
from typing import Annotated

import structlog
from anafpy.exceptions import AnafError
from cyclopts import App, Parameter
from cyclopts.validators import Number

from . import __version__
from .config import (
    AuthSettings,
    default_config_path,
    default_state_path,
    load_config,
    write_default_config,
)
from .engine import run_sync
from .logsink import LogMode, resolve_mode, system_log_handler
from .scheduling import ScheduleError
from .scheduling import install as schedule_install
from .scheduling import status as schedule_status
from .scheduling import uninstall as schedule_uninstall
from .state import SyncState

app = App(
    name="anaf-sync",
    help="Archive RO e-Factura invoices locally, on a schedule.",
    version=f"anaf-sync {__version__}",
    # `main` owns the exit-code mapping (AnafError -> 1, Ctrl-C -> 130), the
    # same shape as anafpy's CLI: commands hand their exit codes back instead
    # of sys.exit()-ing inside cyclopts, and KeyboardInterrupt propagates to
    # `main`'s handler.
    result_action="return_value",
    suppress_keyboard_interrupt=False,
)
# The meta app (the `--verbose` launcher below) is auto-created, so its
# Ctrl-C behaviour must be set by assignment: without this it turns
# KeyboardInterrupt into SystemExit(130) before `main`'s handler sees it.
app.meta.suppress_keyboard_interrupt = False

schedule_app = App(
    name="schedule", help="Manage the OS-level schedule for `anaf-sync sync`."
)
app.command(schedule_app)

ConfigOption = Annotated[
    Path | None,
    Parameter(
        name=["--config", "-c"],
        env_var="ANAF_SYNC_CONFIG",
        help="Config file (default: $ANAF_SYNC_CONFIG or the platform config dir).",
    ),
]


# Set by `_launcher`; `system` on scheduled (non-TTY) runs, where boundary
# errors must also reach the OS log because stderr goes nowhere.
_log_mode = LogMode.CONSOLE
logger = structlog.get_logger(__name__)


def _configure_logging(verbose: bool, mode: LogMode) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if mode is LogMode.CONSOLE:
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(level),
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="%H:%M:%S"),
                structlog.dev.ConsoleRenderer(),
            ],
        )
        return
    # System mode: route structlog through stdlib logging into the native
    # sink. The sink records timestamp and severity itself, so the message
    # carries only the event and its key-value pairs.
    structlog.configure(
        processors=[structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    handler = system_log_handler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.LogfmtRenderer(key_order=["event"]),
            ],
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    # A crash (anything outside the AnafError boundary) must land in the OS
    # log too — its traceback would otherwise vanish with stderr.
    sys.excepthook = _log_crash


def _log_crash(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: TracebackType | None,
) -> None:
    logger.critical(
        "run_crashed",
        error=f"{exc_type.__name__}: {exc}",
        traceback="".join(traceback.format_exception(exc_type, exc, tb)),
    )
    sys.__excepthook__(exc_type, exc, tb)


def _fail(message: str) -> int:
    """Report a boundary error on stderr and, when scheduled, the OS log."""
    print(f"error: {message}", file=sys.stderr)
    if _log_mode is LogMode.SYSTEM:
        logger.error("run_failed", error=message)
    return 1


def _resolve_config_path(option: Path | None) -> Path:
    return option.expanduser() if option is not None else default_config_path()


@app.meta.default
def _launcher(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    verbose: Annotated[
        bool, Parameter(name=["--verbose", "-v"], negative="", help="Debug logging.")
    ] = False,
) -> int:
    """Archive RO e-Factura invoices locally, on a schedule."""
    global _log_mode
    try:
        _log_mode = resolve_mode()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _configure_logging(verbose, _log_mode)
    result = app(tokens)
    return result if isinstance(result, int) else 0


@app.command
def version() -> int:
    """Print the anaf-sync version."""
    print(f"anaf-sync {__version__}")
    return 0


@app.command
def init(
    *,
    config: ConfigOption = None,
    force: Annotated[
        bool, Parameter(negative="", help="Overwrite an existing config.")
    ] = False,
) -> int:
    """Write a commented default configuration file."""
    path = _resolve_config_path(config)
    try:
        write_default_config(path, force=force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    print("Edit it (CIF, output folder, template), then run: anaf-sync sync")
    return 0


@app.command
async def sync(
    *,
    config: ConfigOption = None,
    days: Annotated[
        int | None,
        Parameter(
            validator=Number(gte=1, lte=60), help="Override the lookback window."
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Parameter(negative="", help="List what would be downloaded; write nothing."),
    ] = False,
    redownload: Annotated[
        bool,
        Parameter(negative="", help="Ignore the state file and fetch everything."),
    ] = False,
) -> int:
    """Download all new e-Factura invoices into the archive."""
    config_path = _resolve_config_path(config)
    # AnafError propagates to `main`, which formats it identically.
    try:
        cfg = load_config(config_path)
        provider = AuthSettings.from_env().build_provider()
        state = SyncState.load(default_state_path())
        report = await run_sync(
            cfg,
            provider,
            state,
            days=days,
            dry_run=dry_run,
            redownload=redownload,
        )
    except (FileNotFoundError, ValueError) as exc:
        return _fail(str(exc))

    if _log_mode is LogMode.SYSTEM:
        # One queryable summary event per run; the engine already logged the
        # per-message events, including each failure.
        logger.info(
            "sync_done",
            listed=report.listed,
            new=report.downloaded,
            already_archived=report.already_archived,
            non_invoice=report.skipped_non_invoice,
            failures=len(report.failures),
        )
    print(
        f"listed {report.listed} | new {report.downloaded} | "
        f"already archived {report.already_archived} | "
        f"non-invoice {report.skipped_non_invoice}"
        + (f" | would download {report.would_download}" if dry_run else "")
    )
    if report.failures:
        for message_id, error in report.failures:
            print(f"failed {message_id}: {error}", file=sys.stderr)
        return 1
    return 0


@app.command
def status(*, config: ConfigOption = None) -> int:
    """Show configuration, archive state, and schedule status."""
    config_path = _resolve_config_path(config)
    print(f"config:   {config_path} ({'ok' if config_path.exists() else 'MISSING'})")
    state = SyncState.load(default_state_path())
    print(f"state:    {state.path} ({state.count} messages archived)")
    for message_id, failure in state.failures.items():
        print(
            f"failing:  {message_id} — {failure.attempts} run(s) since "
            f"{failure.first_failed_at:%Y-%m-%d}: {failure.error}"
        )
    if config_path.exists():
        cfg = load_config(config_path)
        print(f"cifs:     {', '.join(cfg.cifs)}  [{cfg.direction.value}]")
        print(f"output:   {cfg.output.resolved_directory}")
        print(f"template: {cfg.output.template}")
    print(f"schedule: {schedule_status()}")
    return 0


@schedule_app.command(name="install")
def schedule_install_cmd(
    *,
    every: Annotated[
        str | None, Parameter(help="Run interval, e.g. 30m, 6h, 1d.")
    ] = None,
    daily_at: Annotated[
        str | None, Parameter(help="Run once a day at HH:MM (24h).")
    ] = None,
) -> int:
    """Register `anaf-sync sync` with the OS scheduler.

    Uses Task Scheduler on Windows, a systemd user timer on Linux, and
    launchd on macOS.
    """
    try:
        print(schedule_install(every=every, daily_at=daily_at))
    except ScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


@schedule_app.command(name="remove")
def schedule_remove_cmd() -> int:
    """Remove the scheduled job."""
    try:
        print(schedule_uninstall())
    except ScheduleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


@schedule_app.command(name="status")
def schedule_status_cmd() -> int:
    """Show whether the scheduled job is installed."""
    print(schedule_status())
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        result = app.meta(argv)
    except AnafError as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        return 130
    # --help/--version take the None branch; commands always return their code.
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
