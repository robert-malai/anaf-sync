"""Where the UI persists its own state — window geometry and header layout.

Deliberately not ``config.toml`` (DESIGN.md §10): geometry is UI state, not
sync configuration, and a file the design promises to round-trip only on
explicit saves must not churn on every resize.

One factory, so both windows share a single seam — and so tests have exactly
one thing to redirect. They need that seam: ``QSettings.setDefaultFormat`` is
*not* enough on macOS, where the ``(organization, application)`` constructor
keeps returning a ``NativeFormat`` store (a plist under
``~/Library/Preferences``) whatever the default format says, so a suite that
only sets the default format ends up writing to the developer's real store.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

__all__ = ["geometry_settings"]

_ORGANIZATION = "anaf-sync"
_APPLICATION = "tray"


def geometry_settings() -> QSettings:
    """The platform-native store for UI state (plist / registry / ini)."""
    return QSettings(_ORGANIZATION, _APPLICATION)
