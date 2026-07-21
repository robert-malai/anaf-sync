"""The ``anaf-sync`` command-line interface."""

from __future__ import annotations

import datetime as dt
import logging
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Annotated, Literal

import structlog
from anafpy.exceptions import AnafError
from cyclopts import App, Parameter
from cyclopts.validators import Number

from . import __version__
from .autostart import AutostartError
from .autostart import install as autostart_install
from .autostart import remove as autostart_remove
from .autostart import status as autostart_status
from .config import (
    AuthSettings,
    default_config_path,
    default_state_path,
    load_config,
    write_default_config,
)
from .engine import SyncReport, run_sync
from .health import days_until_purge
from .lock import LockHeldError, sync_lock
from .logsink import LogMode, resolve_mode, system_log_handler
from .scheduling import ScheduleError
from .scheduling import install as schedule_install
from .scheduling import status as schedule_status
from .scheduling import uninstall as schedule_uninstall
from .state import Archive, RunRecord

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

tray_app = App(
    name="tray", help="Manage login-time autostart for the desktop tray companion."
)
app.command(tray_app)

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
    _record_run(
        default_state_path(),
        outcome="crashed",
        error=f"{exc_type.__name__}: {exc}",
        error_kind=exc_type.__name__,
    )
    sys.__excepthook__(exc_type, exc, tb)


def _record_run(
    state_path: Path,
    *,
    outcome: Literal["ok", "failed", "crashed"],
    report: SyncReport | None = None,
    error: str | None = None,
    error_kind: str | None = None,
) -> None:
    """Persist the last-run record; never let a bookkeeping error mask the run.

    Opens its own short-lived connection so it works on every exit path,
    including the ones where the sync's own ``Archive`` was never opened (a
    missing config) or has already closed.
    """
    try:
        run = RunRecord(
            finished_at=dt.datetime.now(dt.UTC),
            outcome=outcome,
            listed=report.listed if report else 0,
            archived=report.downloaded if report else 0,
            failures=len(report.failures) if report else 0,
            error=error,
            error_kind=error_kind,
        )
        with Archive.open(state_path) as state:
            state.record_run(run)
    except Exception:  # noqa: BLE001 — bookkeeping must never crash the run
        logger.warning("record_run_failed", exc_info=True)


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
        Parameter(negative="", help="Ignore the dedupe gate and fetch everything."),
    ] = False,
) -> int:
    """Download all new e-Factura invoices into the archive."""
    config_path = _resolve_config_path(config)
    state_path = default_state_path()
    # Boundary errors are caught here (not left to `main`) so the last-run
    # record captures their kind — the tray reads it to tell a broken auth
    # from a merely-failing download.
    try:
        cfg = load_config(config_path)
        provider = AuthSettings.from_env().build_provider()
        # One run at a time: overlapping scheduled passes would download the
        # same messages twice and race each other's writes to the archive DB.
        with sync_lock(state_path.with_name("sync.lock")):
            retention = (
                None if dry_run else dt.timedelta(days=cfg.failure_retention_days)
            )
            with Archive.open(state_path, failure_retention=retention) as state:
                report = await run_sync(
                    cfg,
                    provider,
                    state,
                    days=days,
                    dry_run=dry_run,
                    redownload=redownload,
                )
    except (FileNotFoundError, ValueError, LockHeldError, AnafError) as exc:
        # A dry run touches no state, including the last-run record.
        if not dry_run:
            _record_run(
                state_path,
                outcome="failed",
                error=str(exc),
                error_kind=type(exc).__name__,
            )
        return _fail(str(exc))

    if not dry_run:
        _record_run(
            state_path,
            outcome="ok" if report.ok else "failed",
            report=report,
            error=(
                f"{len(report.failures)} download(s) failed"
                if report.failures
                else None
            ),
        )

    if _log_mode is LogMode.SYSTEM:
        # One queryable summary event per run; the engine already logged the
        # per-message events, including each failure.
        logger.info(
            "sync_done",
            listed=report.listed,
            new=report.downloaded,
            already_archived=report.already_archived,
            non_invoice=report.skipped_non_invoice,
            missing_id=report.missing_id,
            failures=len(report.failures),
        )
    print(
        f"listed {report.listed} | new {report.downloaded} | "
        f"already archived {report.already_archived} | "
        f"non-invoice {report.skipped_non_invoice}"
        + (f" | missing id {report.missing_id}" if report.missing_id else "")
        + (f" | would download {report.would_download}" if dry_run else "")
    )
    if not report.ok:
        for message_id, error in report.failures:
            print(f"failed {message_id}: {error}", file=sys.stderr)
        return 1
    return 0


@app.command
def status(*, config: ConfigOption = None) -> int:
    """Show configuration, archive state, and schedule status."""
    config_path = _resolve_config_path(config)
    print(f"config:   {config_path} ({'ok' if config_path.exists() else 'MISSING'})")
    auth = AuthSettings.from_env()
    credentials = "ok" if auth.client_id and auth.client_secret else "MISSING"
    print(f"auth:     ANAFPY_CLIENT_ID/SECRET {credentials}")
    now = dt.datetime.now(dt.UTC)
    state_path = default_state_path()
    # Read-only: a diagnostic command must not create the state dir/schema as
    # a side effect on a machine that has never synced.
    if not state_path.exists():
        print(f"state:    {state_path} (no archive yet)")
    else:
        with Archive.open_readonly(state_path) as archive:
            print(f"state:    {archive.path} ({archive.count} messages archived)")
            last = archive.last_run()
            if last is not None:
                summary = f"{last.error} ({last.error_kind})" if last.error else "ok"
                print(
                    f"last run: {last.finished_at:%Y-%m-%d %H:%M} {last.outcome} — "
                    f"listed {last.listed}, new {last.archived}, "
                    f"failures {last.failures}: {summary}"
                )
            for message_id, failure in archive.failures.items():
                days = days_until_purge(failure, now)
                print(
                    f"failing:  {message_id} — {failure.attempts} run(s) since "
                    f"{failure.first_failed_at:%Y-%m-%d}: {failure.error}"
                )
                print(f"          expires from SPV in {days} day(s)")
    if config_path.exists():
        # status is the diagnostic command — a broken config must be reported
        # in-line, never crash it with a traceback.
        try:
            cfg = load_config(config_path)
        except ValueError as exc:
            print(f"config:   INVALID — {exc}")
        else:
            print(f"cifs:     {', '.join(cfg.cifs)}  [{cfg.direction.value}]")
            print(f"output:   {cfg.output.resolved_directory}")
            print(f"template: {cfg.output.template}")
    print(f"schedule: {schedule_status()}")
    return 0


def _print_or_fail(action: Callable[[], str]) -> int:
    """Print the action's one-line outcome; map its domain error to exit 1."""
    try:
        print(action())
    except (ScheduleError, AutostartError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
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
    return _print_or_fail(lambda: schedule_install(every=every, daily_at=daily_at))


@schedule_app.command(name="remove")
def schedule_remove_cmd() -> int:
    """Remove the scheduled job."""
    return _print_or_fail(schedule_uninstall)


@schedule_app.command(name="status")
def schedule_status_cmd() -> int:
    """Show whether the scheduled job is installed."""
    print(schedule_status())
    return 0


@tray_app.command(name="install")
def tray_install_cmd() -> int:
    """Enable the desktop tray companion at login (idempotent)."""
    return _print_or_fail(autostart_install)


@tray_app.command(name="remove")
def tray_remove_cmd() -> int:
    """Disable tray autostart."""
    return _print_or_fail(autostart_remove)


@tray_app.command(name="status")
def tray_status_cmd() -> int:
    """Show whether tray autostart is enabled."""
    print(autostart_status())
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
