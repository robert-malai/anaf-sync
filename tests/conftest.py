"""Shared test setup: load a repo-root ``.env`` (if present) for the live suite.

The unit suites are credential-free; only the ``live``-marked tests read these
variables. Values already present in the environment win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

# Run Qt headless in tests/CI: the tray suites (pytest-qt) must never try to
# reach a real display. Harmless when PySide6 is not installed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ENV_FILE = Path(__file__).parent.parent / ".env"


def _load_dotenv(path: Path) -> None:
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


if _ENV_FILE.is_file():
    _load_dotenv(_ENV_FILE)
