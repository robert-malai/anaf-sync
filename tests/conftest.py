"""Shared test setup: load a repo-root ``.env`` (if present) for the live suite.

The unit suites are credential-free; only the ``live``-marked tests read these
variables. Values already present in the environment win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Run Qt headless in tests/CI: the tray suites (pytest-qt) must never try to
# reach a real display. Harmless when PySide6 is not installed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def _isolate_qsettings(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Point the tray's UI-state store at a throwaway ini file for the session.

    Both windows persist geometry through ``store.geometry_settings``; without
    this redirect the suite would read and write the developer's real per-user
    store. Redirecting the factory rather than ``QSettings.setDefaultFormat``
    is deliberate: on macOS the ``(organization, application)`` constructor
    ignores the default format and still resolves to a ``NativeFormat`` plist.
    No-op when the ``tray`` extra (PySide6) is absent.
    """
    try:
        from PySide6.QtCore import QSettings
    except ImportError:
        return
    from anaf_sync.tray import store

    path = str(tmp_path_factory.mktemp("qsettings") / "tray.ini")
    store.geometry_settings = lambda: QSettings(  # type: ignore[assignment]
        path, QSettings.Format.IniFormat
    )


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
