"""The anaf-sync desktop tray companion — an optional, GUI-free-by-default add-on.

Importing this package never requires PySide6; only :func:`main` (the
``anaf-sync-tray`` console-script entry point) does. When the ``tray`` extra is
not installed it prints a one-line install hint instead of a traceback, so the
core ``anaf-sync`` CLI keeps working without Qt.
"""

from __future__ import annotations

import sys

__all__ = ["main"]


def main() -> int:
    """Console-script entry point; guards the optional PySide6 dependency."""
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print('instalați cu: pip install "anaf-sync[tray]"', file=sys.stderr)
        return 1
    # Import only after the guard, so a genuine bug in `app` still surfaces as a
    # real error rather than being mistaken for a missing dependency.
    from .app import run

    return run()
