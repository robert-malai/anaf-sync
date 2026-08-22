"""PyInstaller entry point for the ``anaf-sync`` CLI inside the tray bundle.

The bundle ships *two* executables, and this is the second one. It is not a
convenience: :mod:`anaf_sync.tray.runner` spawns ``anaf-sync`` as a child for
every command the tray offers, and :mod:`anaf_sync.scheduling` registers that
same executable with the OS scheduler — so a bundle carrying only the tray
would ship a "Sincronizează acum" button that cannot work and a schedule that
cannot be installed.

A thin launcher, mirroring ``tray_entry.py``: it returns the exit code of the
same ``main`` the ``anaf-sync`` console script calls.
"""

import sys

from anaf_sync.cli import main

if __name__ == "__main__":
    sys.exit(main())
