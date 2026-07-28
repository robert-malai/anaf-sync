"""The anaf-sync desktop tray companion — an optional, GUI-free-by-default add-on.

Importing this package never requires PySide6; only :func:`main` (the
``anaf-sync-tray`` gui-script entry point) does. When the ``tray`` extra is
not installed it prints a one-line install hint instead of a traceback, so the
core ``anaf-sync`` CLI keeps working without Qt.

Launched from a terminal on macOS/Linux, :func:`main` respawns itself in a new
session and returns immediately, so the shell gets its prompt back and closing
the terminal cannot take the tray down with it. On Windows the gui-script
launcher (pythonw) already yields the console, and service-manager contexts
(launchd, XDG autostart) have no tty, so both run the app attached.
"""

from __future__ import annotations

import subprocess
import sys

__all__ = ["main"]

_FOREGROUND_FLAG = "--foreground"


def main() -> int:
    """Entry point for ``anaf-sync-tray``; guards the optional PySide6 dependency."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print('instalați cu: pip install "anaf-sync[tray]"', file=sys.stderr)
        return 1
    if _holds_a_terminal():
        _relaunch_detached()
        return 0
    # Import only after the guard, so a genuine bug in `app` still surfaces as a
    # real error rather than being mistaken for a missing dependency.
    from .app import run

    return run()


def _holds_a_terminal() -> bool:
    """True when running attached would tie the tray to an open terminal.

    Windows never qualifies (the gui-script launcher frees the shell), nor do
    frozen bundles (windowed, launched by Finder/Explorer) or manager contexts
    like launchd and XDG autostart, whose stdio is not a tty — those must stay
    attached so the manager's view of the process remains honest.
    """
    if sys.platform == "win32" or getattr(sys, "frozen", False):
        return False
    if _FOREGROUND_FLAG in sys.argv[1:]:
        return False
    return any(
        stream is not None and stream.isatty()
        for stream in (sys.stdin, sys.stdout, sys.stderr)
    )


def _relaunch_detached() -> None:
    """Respawn this entry point in its own session, handing the terminal back.

    ``--foreground`` marks the child as already detached; a new session plus
    devnull stdio is what severs it from the terminal's lifetime.
    """
    subprocess.Popen(
        [sys.argv[0], _FOREGROUND_FLAG],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
