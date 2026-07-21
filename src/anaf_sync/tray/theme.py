"""Visual tokens and QSS generation — the single home for every colour.

The design handoff's token table, transcribed once into light/dark
:class:`Theme` dataclasses (the plan forbids hex literals scattered in
widgets). Colour values and QSS-string building are pure and testable; only
:func:`current_theme` / :func:`on_scheme_changed` touch Qt, at the edge.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Literal

from ..health import HealthState

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "DARK",
    "LIGHT",
    "Theme",
    "current_theme",
    "menu_qss",
    "on_scheme_changed",
    "status_color",
    "theme_for_scheme",
    "window_qss",
]


@dataclasses.dataclass(frozen=True)
class Theme:
    """One colour scheme (all values are ``#rrggbb`` strings)."""

    name: Literal["light", "dark"]
    window_bg: str
    panel_bg: str
    border: str
    border_strong: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_soft_bg: str
    on_accent: str
    row_hover: str
    row_selected: str
    green: str
    green_bg: str
    amber: str
    amber_bg: str
    red: str
    red_bg: str
    mono_chip_bg: str


# The token table (handoff §Design Tokens), verbatim.
LIGHT = Theme(
    name="light",
    window_bg="#f4f6f8",
    panel_bg="#ffffff",
    border="#d8dee6",
    border_strong="#c4ccd6",
    text="#1c2733",
    muted="#5b6b7c",
    faint="#8494a5",
    accent="#33658A",
    accent_soft_bg="#e3ecf3",
    on_accent="#ffffff",
    row_hover="#eef2f6",
    row_selected="#dfe9f1",
    green="#2E7D46",
    green_bg="#e4f1e9",
    amber="#B3640F",
    amber_bg="#f8eedd",
    red="#B3312D",
    red_bg="#f8e8e7",
    mono_chip_bg="#eef1f5",
)

DARK = Theme(
    name="dark",
    window_bg="#1b2128",
    panel_bg="#232a33",
    border="#323c48",
    border_strong="#3d4855",
    text="#e4e9ef",
    muted="#95a3b3",
    faint="#6d7c8c",
    accent="#5f92bd",
    accent_soft_bg="#28394a",
    on_accent="#0f1a24",
    row_hover="#28303a",
    row_selected="#2c3b4a",
    green="#5cb87f",
    green_bg="#20332a",
    amber="#d99b4e",
    amber_bg="#39301f",
    red="#e07672",
    red_bg="#3c2624",
    mono_chip_bg="#1d242c",
)

# Radii (handoff §Design Tokens), in px — shared across both schemes.
RADIUS_PANEL = 9
RADIUS_BUTTON = 6
RADIUS_PILL = 9
RADIUS_CHIP = 6

#: Monospace stack for paths, templates, CIFs, identifiers, filenames.
MONO_FONT_FAMILY = "ui-monospace, Menlo, Consolas, monospace"


def theme_for_scheme(*, dark: bool) -> Theme:
    """Pick the matching :class:`Theme` for a light/dark preference."""
    return DARK if dark else LIGHT


def status_color(theme: Theme, state: HealthState) -> str:
    """The status-dot colour for a health state (green / amber / red)."""
    return {"ok": theme.green, "warn": theme.amber, "err": theme.red}[state]


def status_bg(theme: Theme, state: HealthState) -> str:
    """The tinted alert-row background for a health state."""
    return {"ok": theme.green_bg, "warn": theme.amber_bg, "err": theme.red_bg}[state]


def menu_qss(theme: Theme) -> str:
    """QSS for the tray :class:`QMenu`: panel background, accent hover rows."""
    return f"""
QMenu {{
    background-color: {theme.panel_bg};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: {RADIUS_PANEL}px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 14px 7px 14px;
    border-radius: {RADIUS_BUTTON}px;
    background: transparent;
}}
QMenu::item:selected {{
    background-color: {theme.accent};
    color: {theme.on_accent};
}}
QMenu::item:disabled {{
    color: {theme.faint};
}}
QMenu::separator {{
    height: 1px;
    background: {theme.border};
    margin: 6px 8px;
}}
"""


def window_qss(theme: Theme) -> str:
    """Base QSS for the main window shell (extended per-view in later milestones)."""
    return f"""
QWidget {{
    background-color: {theme.window_bg};
    color: {theme.text};
}}
QToolTip {{
    background-color: {theme.panel_bg};
    color: {theme.text};
    border: 1px solid {theme.border};
}}
"""


def current_theme() -> Theme:
    """The theme matching the running app's colour scheme (Qt edge).

    Falls back to light when no ``QGuiApplication`` exists yet or the platform
    reports ``Unknown``.
    """
    from PySide6.QtGui import QGuiApplication  # local: keep import off core path

    app = QGuiApplication.instance()
    if app is None:
        return LIGHT
    from PySide6.QtCore import Qt

    scheme = QGuiApplication.styleHints().colorScheme()
    return theme_for_scheme(dark=scheme == Qt.ColorScheme.Dark)


def on_scheme_changed(callback: Callable[[Theme], None]) -> None:
    """Invoke ``callback`` with the new :class:`Theme` whenever the OS scheme flips."""
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.styleHints().colorSchemeChanged.connect(
        lambda _scheme: callback(current_theme())
    )
