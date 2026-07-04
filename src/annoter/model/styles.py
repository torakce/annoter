"""Cross-cutting style enums shared by items, properties dock, and persistence.

Kept out of the views layer so the persistence layer (services/) can
import them without pulling Qt.
"""

from __future__ import annotations

from enum import Enum


class DashStyle(Enum):
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    DASH_DOT = "dash_dot"
    DASH_DOT_DOT = "dash_dot_dot"


# Pen-pattern array (in stroke-width units) used when we need to drive
# either Qt's setDashPattern() or PDF's /Border /D entry.
DASH_PATTERNS: dict[DashStyle, list[float]] = {
    DashStyle.SOLID: [],
    DashStyle.DASHED: [4.0, 4.0],
    DashStyle.DOTTED: [1.0, 3.0],
    DashStyle.DASH_DOT: [4.0, 2.0, 1.0, 2.0],
    DashStyle.DASH_DOT_DOT: [4.0, 2.0, 1.0, 2.0, 1.0, 2.0],
}


class EndStyle(Enum):
    """Line-end / arrow-head decoration. Names mirror PDF /LE values,
    plus datum-style triangles (no PDF equivalent; exported as
    ClosedArrow for external viewers, exact style kept in our JSON)."""

    NONE = "none"
    OPEN_ARROW = "open_arrow"
    CLOSED_ARROW = "closed_arrow"
    BUTT = "butt"
    DIAMOND = "diamond"
    CIRCLE = "circle"
    SQUARE = "square"
    SLASH = "slash"
    # Flat-based isoceles triangle at the very endpoint, apex toward the
    # line: the GD&T datum-feature symbol (ISO 5459), hollow or filled.
    TRIANGLE = "triangle"
    TRIANGLE_FILLED = "triangle_filled"


# Display order + labels shared by the Properties dock combos and the
# endpoint context menu.
END_STYLE_LABELS: list[tuple[EndStyle, str]] = [
    (EndStyle.NONE, "None"),
    (EndStyle.OPEN_ARROW, "Open arrow"),
    (EndStyle.CLOSED_ARROW, "Closed arrow"),
    (EndStyle.TRIANGLE, "Triangle (datum, hollow)"),
    (EndStyle.TRIANGLE_FILLED, "Triangle (datum, filled)"),
    (EndStyle.BUTT, "Butt (perp. tick)"),
    (EndStyle.SLASH, "Slash"),
    (EndStyle.DIAMOND, "Diamond"),
    (EndStyle.CIRCLE, "Circle"),
    (EndStyle.SQUARE, "Square"),
]


class TextAlign(Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class TextBorder(Enum):
    """Optional outline drawn around a text (or a line's end label).

    ELLIPSE renders as a true CIRCLE (user feedback); the enum value is
    kept as "ellipse" for persistence compatibility.
    """

    NONE = "none"
    BOX = "box"
    ELLIPSE = "ellipse"


TEXT_BORDER_LABELS: list[tuple[TextBorder, str]] = [
    (TextBorder.NONE, "None"),
    (TextBorder.BOX, "Box"),
    (TextBorder.ELLIPSE, "Circle"),
]


class HandleRole(Enum):
    """Identifies a resize handle on a selected annotation item."""

    TOP_LEFT = "tl"
    TOP = "t"
    TOP_RIGHT = "tr"
    RIGHT = "r"
    BOTTOM_RIGHT = "br"
    BOTTOM = "b"
    BOTTOM_LEFT = "bl"
    LEFT = "l"
    # Line / arrow endpoints.
    P1 = "p1"
    P2 = "p2"
