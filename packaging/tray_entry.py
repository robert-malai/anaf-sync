"""PyInstaller entry point for the tray bundle.

A thin launcher so the frozen executable behaves exactly like the
``anaf-sync-tray`` console script: it goes through the package guard in
``anaf_sync.tray`` (which requires PySide6) and returns its exit code.
"""

import sys

from anaf_sync.tray import main

if __name__ == "__main__":
    sys.exit(main())
