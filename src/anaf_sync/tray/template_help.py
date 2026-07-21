"""The *Șablon de denumire* variable reference — a collapsible legend.

The template field is the only control in Setări that assumes a vocabulary the
UI never shows, so the form used to teach that vocabulary by punishment: type
``{numer}``, get a red box and a dead save button. This panel lists the legal
names instead, collapsed by default (handoff §3).

Its one idea: the third column is **rendered, not written**. Every sample value
comes from ``PathTemplate("{name}").render(sample_context())`` — the production
formatter, sanitiser and placeholder logic — against the very sample invoice the
green preview box above is rendering. So the legend is not documentation *about*
the variables, it is the preview's invoice decomposed, and it cannot drift from
what a real sync would write. That is also why ``{created}`` shows
``2026-07-06 09-30-00``: ``:`` is illegal in a Windows path, and seeing the ugly
form is the fastest argument for ``{created:%H%M}``.

Descriptions are Romanian UI copy and live here rather than in ``context.py``;
the *names* do not — :data:`GROUPS` is checked against
:func:`~anaf_sync.tray.preview.sample_context` by the suite, so a variable added
to the context without documenting it fails a test instead of quietly producing
another stale list.
"""

from __future__ import annotations

import dataclasses

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..template import PathTemplate
from .flowgrid import SPACING, ColumnGrid, GroupGrid
from .preview import sample_context
from .theme import MONO_FONT_FAMILY, Theme

__all__ = ["GROUPS", "TemplateHelp", "rendered_samples", "template_help_qss"]


@dataclasses.dataclass(frozen=True)
class Variable:
    """One template variable: its name, Romanian gloss, and provenance."""

    name: str
    description: str
    #: Comes from the parsed UBL invoice, so it renders ``unknown`` for
    #: messages that carry none (error files, buyer messages). Marked with ●.
    from_xml: bool = True


@dataclasses.dataclass(frozen=True)
class Group:
    """A titled run of variables, in the order an operator thinks about them."""

    title: str
    variables: tuple[Variable, ...]


#: The template vocabulary, exactly. ``total`` and ``seller_*``/``buyer_*`` are
#: absent because they left the context — see ``context.build_context``.
GROUPS: tuple[Group, ...] = (
    Group(
        "Factura",
        (
            Variable("number", "Numărul facturii"),
            Variable("issue_date", "Data emiterii"),
            Variable("issue_month", "Luna emiterii, în litere"),
            Variable("due_date", "Data scadenței"),
            Variable("kind", "Tipul documentului"),
            Variable("currency", "Moneda"),
        ),
    ),
    Group(
        "Partener",
        (
            Variable("partner_name", "Partenerul — după direcție"),
            # Falls back to ANAF's sender/receiver CIF without a parsed view, so
            # it usually resolves anyway — but "poate fi unknown" is the safe
            # reading, and a third marker state would cost more than it explains.
            Variable("partner_cif", "CIF-ul partenerului"),
            Variable("cif", "CIF-ul urmărit — compania ta", from_xml=False),
            Variable("direction", "received sau sent", from_xml=False),
        ),
    ),
    Group(
        "Mesaj SPV",
        (
            Variable("message_id", "ID-ul mesajului în SPV", from_xml=False),
            Variable("request_id", "ID-ul descărcării", from_xml=False),
            Variable("message_type", "Tipul mesajului la ANAF", from_xml=False),
            Variable("created", "Momentul încărcării în SPV", from_xml=False),
            Variable("created_month", "Luna încărcării, în litere", from_xml=False),
        ),
    ),
)

#: The format-specifier strip, pinned below the scrolling list. Two rows, not
#: three: with ``total`` gone every variable is a date or a string, so there is
#: no numeric formatting left to document. Outputs are asserted by the suite
#: against the real renderer, never trusted as literals.
SPECIFIERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Date",
        (
            "{issue_date:%Y}",
            "{issue_date:%m}",
            "{issue_date:%Y-%m-%d}",
            "{created:%H%M}",
        ),
    ),
    (
        "Litere",
        (
            "{issue_month!u}",
            "{issue_month!c}",
            "{issue_month!l}",
            "{partner_name!t}",
        ),
    ),
)

_LEAD = (
    "Click pe o variabilă pentru a o insera în șablon. "
    "Valorile sunt cele ale facturii-exemplu din previzualizare."
)
_LEGEND = (
    "se completează din XML-ul facturii; pentru mesaje fără XML "
    "(fișiere de eroare, mesaje de la cumpărător) devin unknown. "
    "Datele acceptă orice format strftime."
)
#: Expanding must never resize the window: the list caps here and scrolls
#: internally, so the pinned save bar survives the 620px minimum height. Set
#: just above what the 3-column layout needs (~264px measured, plus slack for
#: wider font metrics), so past the reflow breakpoint the whole list is visible
#: at once and only the stacked 1-column layout scrolls — which is the point of
#: having a wide layout at all.
_MAX_CARD_HEIGHT = 300
#: The strip's "DATE"/"LITERE" gutter.
_STRIP_KEY_WIDTH = 60
#: One row of four examples is the most the strip ever shows; beyond that the
#: eye stops reading it as a row of examples.
_MAX_SPECIFIER_COLUMNS = 4
_SETTINGS_KEY = "settings/variableHelpExpanded"


def render_sample(expression: str) -> str:
    """Render one ``{name}``/``{name:spec}`` against the sample invoice.

    The whole point of the panel: production rendering, so the legend and a real
    sync can never disagree. Returns the rendered text, or ``expression``
    unchanged if it cannot render (an impossible state the suite pins down, but
    a legend must never raise into the form).
    """
    try:
        return str(PathTemplate(expression).render(sample_context()))
    except Exception:  # a reference panel is never worth crashing Setări for
        return expression


def rendered_samples() -> dict[str, str]:
    """Every documented variable mapped to what it renders to."""
    return {
        variable.name: render_sample("{" + variable.name + "}")
        for group in GROUPS
        for variable in group.variables
    }


def variable_count() -> int:
    """How many variables the panel documents (its header badge)."""
    return sum(len(group.variables) for group in GROUPS)


class _SpecifierGrid(ColumnGrid):
    """The strip's example chips: as many per row as actually fit, up to four.

    Not the 882px breakpoint the groups use. The strip is a row of fixed-size
    examples rather than elastic columns, and pinning it to a *guessed* count
    was what made the whole Setări form scroll horizontally at its 760px
    minimum — four chips are ~1040px wide, and even two overflow the field
    column. Packing by the measured chip width can't make that mistake again.
    """

    def columns_for(self, width: int) -> int:
        chip = self.widest_item()
        if width <= 0 or chip <= 0:
            return 1  # the floor `minimumSize` is computed from
        return max(
            1, min(_MAX_SPECIFIER_COLUMNS, (width + SPACING) // (chip + SPACING))
        )


class _FlowHolder(QWidget):
    """A re-flowing widget whose height tracks its width inside a scroll area.

    ``QScrollArea`` sizes a ``widgetResizable`` child from ``sizeHint`` and
    ``minimumSizeHint`` and never consults ``heightForWidth``. A re-flowing
    layout reports its *tallest* arrangement there (1-column), so without this
    the list kept reserving 665px and scrolled at every width — the 3-column
    layout would have been laid out correctly and still scrolled, which is
    exactly the thing the wide layout exists to avoid.
    """

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 — Qt override
        super().resizeEvent(event)
        layout = self.layout()
        if layout is not None:
            self.setMinimumHeight(layout.heightForWidth(self.width()))


class _Clickable(QFrame):
    """A focusable row that emits its payload on click, Space or Enter.

    One widget behind both the variable rows and the specifier examples: they
    differ only in what they show and what text they insert.
    """

    activated = Signal(str)

    def __init__(self, payload: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 — Qt
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.activated.emit(self._payload)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 — Qt override
        if event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self.activated.emit(self._payload)
            return
        super().keyPressEvent(event)


class TemplateHelp(QWidget):
    """The collapsible variable reference under the template preview."""

    #: Text to splice at the template field's caret (``{number}``, ``{total:.2f}``).
    insert_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._toggle = QToolButton()
        self._toggle.setObjectName("helpToggle")
        self._toggle.setCheckable(True)
        self._toggle.setAutoRaise(True)
        self._toggle.setText(f"Variabile disponibile ({variable_count()})")
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._toggle.toggled.connect(self._on_toggled)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._toggle)
        row.addStretch(1)
        outer.addLayout(row)

        self._card = self._build_card()
        outer.addWidget(self._card)

        self._toggle.setChecked(_restore_expanded())
        self._on_toggled(self._toggle.isChecked())

    # -- construction ---------------------------------------------------------

    def _build_card(self) -> QWidget:
        card = QFrame()
        card.setObjectName("helpCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lead = QLabel(_LEAD)
        lead.setObjectName("helpLead")
        lead.setWordWrap(True)
        # Word-wrapped labels are handed a two-line box; without this they sit
        # vertically centred in it and drift away from what they introduce.
        lead.setAlignment(Qt.AlignmentFlag.AlignTop)
        lead.setContentsMargins(12, 8, 12, 2)
        layout.addWidget(lead)
        layout.addWidget(self._build_groups())
        layout.addWidget(self._build_specifiers())
        layout.addWidget(self._build_legend())
        return card

    def _build_legend(self) -> QWidget:
        # The ● gets its own label here too, so the footnote's marker is the
        # same amber as the markers it explains rather than legend-coloured.
        legend = QWidget()
        row = QHBoxLayout(legend)
        row.setContentsMargins(12, 6, 12, 9)
        row.setSpacing(4)
        dot = QLabel("●")
        dot.setObjectName("helpDot")
        dot.setAlignment(Qt.AlignmentFlag.AlignTop)
        text = QLabel(_LEGEND)
        text.setObjectName("helpLegend")
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(dot)
        row.addWidget(text, 1)
        return legend

    def _build_groups(self) -> QWidget:
        samples = rendered_samples()
        holder = _FlowHolder()
        grid = GroupGrid(holder)
        for group in GROUPS:
            column = QWidget()
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setSpacing(0)
            title = QLabel(group.title.upper())
            title.setObjectName("helpGroup")
            title.setContentsMargins(6, 8, 6, 3)
            column_layout.addWidget(title)
            for variable in group.variables:
                column_layout.addWidget(self._variable_row(variable, samples))
            column_layout.addStretch(1)
            grid.addWidget(column)

        scroll = QScrollArea()
        scroll.setObjectName("helpScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(_MAX_CARD_HEIGHT)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder.setContentsMargins(8, 6, 8, 10)
        scroll.setWidget(holder)
        return scroll

    def _variable_row(self, variable: Variable, samples: dict[str, str]) -> QWidget:
        token = "{" + variable.name + "}"
        row = _Clickable(token)
        row.setObjectName("helpRow")
        row.setToolTip(f"Inserează {token} în șablon")
        row.activated.connect(self.insert_requested)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(1)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        name = QLabel(token)
        name.setObjectName("helpName")
        description = QLabel(variable.description)
        description.setObjectName("helpDesc")
        head.addWidget(name)
        head.addWidget(description, 1)
        layout.addLayout(head)

        # The ● rides on the value line, where "this may be unknown" is the
        # warning. Its own label rather than rich text, so the amber comes from
        # the QSS the parent form applies instead of a hex baked in at build.
        tail = QHBoxLayout()
        tail.setContentsMargins(0, 0, 0, 0)
        tail.setSpacing(3)
        if variable.from_xml:
            dot = QLabel("●")
            dot.setObjectName("helpDot")
            tail.addWidget(dot)
        value = QLabel(samples[variable.name])
        value.setObjectName("helpValue")
        tail.addWidget(value)
        tail.addStretch(1)
        layout.addLayout(tail)
        return row

    def _build_specifiers(self) -> QWidget:
        strip = QFrame()
        strip.setObjectName("helpStrip")
        layout = QVBoxLayout(strip)
        layout.setContentsMargins(12, 8, 12, 9)
        layout.setSpacing(3)
        for title, expressions in SPECIFIERS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            key = QLabel(title.upper())
            key.setObjectName("helpGroup")
            key.setFixedWidth(_STRIP_KEY_WIDTH - 8)
            key.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            row.addWidget(key)
            # A grid, not an HBox: four chips side by side are ~700px wide, which
            # at the 760px minimum window would make the *whole form* scroll
            # horizontally rather than the strip wrap.
            chips = QWidget()
            grid = _SpecifierGrid(chips)
            for expression in expressions:
                grid.addWidget(self._specifier_chip(expression))
            row.addWidget(chips, 1)
            layout.addLayout(row)
        return strip

    def _specifier_chip(self, expression: str) -> QWidget:
        chip = _Clickable(expression)
        chip.setObjectName("helpRow")
        chip.setToolTip(f"Inserează {expression} în șablon")
        chip.activated.connect(self.insert_requested)
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(5)
        token = QLabel(expression)
        token.setObjectName("helpSpec")
        arrow = QLabel("→")
        arrow.setObjectName("helpArrow")
        out = QLabel(render_sample(expression))
        out.setObjectName("helpOut")
        layout.addWidget(token)
        layout.addWidget(arrow)
        layout.addWidget(out)
        # The grid hands every chip a full column; without this the three
        # labels spread across it and the arrow drifts away from its example.
        layout.addStretch(1)
        return chip

    # -- interactions ---------------------------------------------------------

    def _on_toggled(self, expanded: bool) -> None:
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._card.setVisible(expanded)
        _store_expanded(expanded)


def _restore_expanded() -> bool:
    """The remembered disclosure state — collapsed until the user opens it."""
    from .store import geometry_settings

    return str(geometry_settings().value(_SETTINGS_KEY, "false")).lower() == "true"


def _store_expanded(expanded: bool) -> None:
    from .store import geometry_settings

    geometry_settings().setValue(_SETTINGS_KEY, "true" if expanded else "false")


def template_help_qss(theme: Theme) -> str:
    """QSS for the panel, appended to the Setări sheet so themes stay in one place."""
    return f"""
#helpToggle {{ color:{theme.muted}; border:none; padding:3px 4px;
    background:transparent; }}
#helpToggle:hover {{ color:{theme.accent}; }}
#helpCard {{ background-color:{theme.panel_bg}; border:1px solid {theme.border};
    border-radius:8px; }}
#helpCard QLabel {{ background:transparent; }}
#helpScroll, #helpScroll > QWidget > QWidget {{ background:transparent;
    border:none; }}
#helpLead {{ color:{theme.faint}; font-size:11px; }}
#helpGroup {{ color:{theme.faint}; font-size:10px; font-weight:700;
    letter-spacing:1px; }}
/* The transparent border is always there so focus only recolours it — a
   border appearing on focus would shift every row below it by a pixel. */
#helpRow {{ border-radius:5px; background:transparent;
    border:1px solid transparent; }}
#helpRow:hover {{ background-color:{theme.row_hover}; }}
#helpRow:focus {{ background-color:{theme.row_hover};
    border-color:{theme.accent}; }}
#helpCard #helpName {{ font-family:{MONO_FONT_FAMILY}; font-size:11px;
    background-color:{theme.mono_chip_bg}; border-radius:5px; padding:1px 5px; }}
/* No `#helpRow:hover #helpName` rule: Qt cannot evaluate a pseudo-state on an
   ancestor in a descendant selector and applies it unconditionally, which
   painted every chip as if it were hovered. The row background carries the
   hover on its own. */
#helpDesc {{ color:{theme.muted}; font-size:11.5px; }}
#helpDot {{ color:{theme.amber}; font-size:9px; }}
#helpValue {{ font-family:{MONO_FONT_FAMILY}; font-size:11px;
    color:{theme.faint}; }}
#helpStrip {{ background-color:{theme.window_bg};
    border-top:1px solid {theme.border}; }}
#helpSpec {{ font-family:{MONO_FONT_FAMILY}; font-size:11px;
    color:{theme.accent}; }}
#helpArrow {{ color:{theme.faint}; font-size:10px; }}
#helpOut {{ font-family:{MONO_FONT_FAMILY}; font-size:11px;
    color:{theme.muted}; }}
#helpLegend {{ color:{theme.faint}; font-size:10.5px; }}
"""
