"""Thin-stroke UI icons (UI redesign), rendered from inline SVG paths.

Each glyph is a list of SVG path strings on a 24 x 24 grid, drawn with
a round-capped 1.6-unit stroke in a single color -- the same geometry
as the design mock-up, so the app and the mock-up read alike. Icons are
rasterized (QSvgRenderer -> QPixmap), so like `views/icons.py` they must
be rebuilt when the theme changes the glyph color.

Annotation-specific pictograms (dash styles, line ends, GD&T symbols)
stay in `views/icons.py`.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# name -> SVG path data (24 x 24 viewBox). Circles are written as two
# arcs so every glyph is plain path data.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "menu": ("M4 7h16M4 12h16M4 17h16",),
    "search": (
        "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z",
        "M20 20l-3.5-3.5",
    ),
    "undo": ("M9 14L4 9l5-5", "M4 9h10.5a5.5 5.5 0 0 1 0 11H11"),
    "redo": ("M15 14l5-5-5-5", "M20 9H9.5a5.5 5.5 0 0 0 0 11H13"),
    "save": ("M5 4h11l3 3v13H5z", "M8 4v5h7V4", "M8 20v-6h8v6"),
    "moon": ("M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z",),
    "sun": (
        "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z",
        "M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4"
        "M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4",
    ),
    "more": (
        "M5 11.2a.8.8 0 1 0 0 1.6a.8.8 0 1 0 0-1.6z",
        "M12 11.2a.8.8 0 1 0 0 1.6a.8.8 0 1 0 0-1.6z",
        "M19 11.2a.8.8 0 1 0 0 1.6a.8.8 0 1 0 0-1.6z",
    ),
    "open": (
        "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5"
        "a2 2 0 0 1-2-2z",
    ),
    "recent": (
        "M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z",
        "M12 8v4l3 2",
    ),
    "export-image": (
        "M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6"
        "a2 2 0 0 1 2-2z",
        "M9 7.5a1.5 1.5 0 1 0 0 3a1.5 1.5 0 1 0 0-3z",
        "M20 15l-5-5-9 10",
    ),
    "insert-pages": ("M6 3h8l4 4v14H6z", "M12 11v6M9 14h6"),
    "file": ("M6 3h8l4 4v14H6z", "M14 3v4h4"),
    "file-upload": (
        "M6 3h8l4 4v14H6z",
        "M14 3v4h4",
        "M12 17v-6M9.5 13.5L12 11l2.5 2.5",
    ),
    "resize": ("M4 9V4h5M20 15v5h-5M4 4l7 7M20 20l-7-7",),
    "keyboard": (
        "M5 6h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8"
        "a2 2 0 0 1 2-2z",
        "M7 10h.5M11 10h.5M15 10h.5M7 14h10",
    ),
    "close-doc": ("M6 6l12 12M18 6L6 18",),
    "chevron-right": ("M9 6l6 6-6 6",),
    "chevron-left": ("M15 6l-6 6 6 6",),
    # ---- canvas navigation pill (Lot D) ----
    "zoom-in": ("M12 6v12M6 12h12",),
    "zoom-out": ("M6 12h12",),
    "fit": ("M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",),
    "zoom-area": (
        "M10.5 4.5a6 6 0 1 0 0 12a6 6 0 1 0 0-12z",
        "M15 15l5 5",
        "M8 10.5h5M10.5 8v5",
    ),
    "rotate-cw": ("M20 11a8 8 0 1 0-2.3 5.7", "M20 4v7h-7"),
    "chevron-down": ("M6 9l6 6 6-6",),
    "check": ("M5 12l5 5 9-10",),
    # ---- drawing tools (tool rail, Lot C) ----
    "select": ("M6 3.5l12.5 7.2-5.6 1.4-2.6 5.4z",),
    "rectangle": (
        "M5.5 6h13a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5h-13"
        "A1.5 1.5 0 0 1 4 16.5v-9A1.5 1.5 0 0 1 5.5 6z",
    ),
    "ellipse": ("M3.5 12a8.5 6.5 0 1 0 17 0a8.5 6.5 0 1 0-17 0z",),
    "cloud": ("M7 18a4 4 0 0 1-.6-8 5 5 0 0 1 9.7-1.4A4.5 4.5 0 0 1 17.5 18z",),
    "polygon": ("M12 3.5l8 6-3 9.5H7l-3-9.5z",),
    "line": ("M5 19L19 5",),
    "arrow": ("M5 19L19 5", "M11 5h8v8"),
    "double-arrow": ("M5 19L19 5", "M12 5h7v7", "M12 19H5v-7"),
    "polyline": (
        "M4 18l5-9 6 5 5-9",
        "M4 16.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4z",
        "M20 3.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4z",
    ),
    "freehand": ("M3 17c3-6 5.5-9.5 7.5-7s-1 7 2.5 6.5 4-8 8-9",),
    "text": ("M5 7V4.5h14V7", "M12 4.5V20", "M9 20h6"),
    "note": ("M5 4h14v10l-6 6H5z", "M13 20v-6h6"),
    "stamp": (
        "M9.5 4h5v4.5l-1 4.5h-3l-1-4.5z",
        "M5 13h14v4H5z",
        "M6 20.5h12",
    ),
    "gdt": (
        "M3.5 7h17a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-17a1 1 0 0 1-1-1V8"
        "a1 1 0 0 1 1-1z",
        "M9 7v10M15.5 7v10",
        "M5.75 10.3a1.7 1.7 0 1 0 0 3.4a1.7 1.7 0 1 0 0-3.4z",
    ),
    # linear dimension: extension lines, arrowed dimension line
    "dimension": (
        "M4 7v10M20 7v10M4 12h16",
        "M7.5 9.5L4.5 12l3 2.5M16.5 9.5l3 2.5-3 2.5",
    ),
    # what a dimension measures: the true length, or its projection
    "dim-aligned": ("M5 19L19 5", "M5 14v5h5", "M14 5h5v5"),
    "dim-horizontal": (
        "M4 7v10M20 7v10M4 12h16",
        "M7.5 9.5L4.5 12l3 2.5M16.5 9.5l3 2.5-3 2.5",
    ),
    "dim-vertical": (
        "M7 4h10M7 20h10M12 4v16",
        "M9.5 7.5L12 4.5l2.5 3M9.5 16.5l2.5 3 2.5-3",
    ),
    "plus": ("M12 5v14M5 12h14",),
    "pencil": ("M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"),
    "trash": ("M5 7h14M10 11v6M14 11v6M6.5 7l1 13h9l1-13M9 7V4h6v3",),
    "duplicate": (
        "M9.5 8h9A1.5 1.5 0 0 1 20 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9"
        "A1.5 1.5 0 0 1 8 18.5v-9A1.5 1.5 0 0 1 9.5 8z",
        "M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9"
        "A1.5 1.5 0 0 0 5.5 16H8",
        "M14 11v6M11 14h6",
    ),
    # Stacking order (Lot J): one family, arrows against a bar for the
    # "all the way" moves, bare arrows for one step.
    "to-front": ("M5 4h14", "M12 20V8", "M7.5 12.5L12 8l4.5 4.5"),
    "to-back": ("M5 20h14", "M12 4v12", "M7.5 11.5L12 16l4.5-4.5"),
    "raise": ("M12 19V6", "M7.5 10.5L12 6l4.5 4.5"),
    "lower": ("M12 5v13", "M7.5 13.5L12 18l4.5-4.5"),
    # clipboard (context menu top row)
    "cut": (
        "M4.5 17a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0z",
        "M14.5 17a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0z",
        "M8.8 15.2L18 4",
        "M15.2 15.2L6 4",
    ),
    "copy": (
        "M9.5 8h9A1.5 1.5 0 0 1 20 9.5v9a1.5 1.5 0 0 1-1.5 1.5h-9"
        "A1.5 1.5 0 0 1 8 18.5v-9A1.5 1.5 0 0 1 9.5 8z",
        "M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9"
        "A1.5 1.5 0 0 0 5.5 16H8",
    ),
    "paste": (
        "M8.5 5H6.5A1.5 1.5 0 0 0 5 6.5v13A1.5 1.5 0 0 0 6.5 21h11"
        "a1.5 1.5 0 0 0 1.5-1.5v-13A1.5 1.5 0 0 0 17.5 5h-2",
        "M9 3.5h6v3.5H9z",
    ),
    # grouping
    "group": (
        "M4 7V4h3M17 4h3v3M20 17v3h-3M7 20H4v-3",
        "M8 8h5v5H8z",
        "M11 11h5v5h-5z",
    ),
    "ungroup": ("M4 4h7v7H4z", "M13 13h7v7h-7z"),
    # align / distribute several annotations
    "align-left": ("M4 3v18", "M7 6h11v4H7z", "M7 14h7v4H7z"),
    "align-center-h": ("M12 3v18", "M6 6h12v4H6z", "M8.5 14h7v4h-7z"),
    "align-right": ("M20 3v18", "M6 6h11v4H6z", "M10 14h7v4h-7z"),
    "align-top": ("M3 4h18", "M6 7h4v11H6z", "M14 7h4v7h-4z"),
    "align-middle-v": ("M3 12h18", "M6 6h4v12H6z", "M14 8.5h4v7h-4z"),
    "align-bottom": ("M3 20h18", "M6 6h4v11H6z", "M14 10h4v7h-4z"),
    "distribute-h": ("M4 4v16", "M20 4v16", "M9.5 8h5v8h-5z"),
    "distribute-v": ("M4 4h16", "M4 20h16", "M8 9.5h8v5H8z"),
    # line points: which end is which (a disc starts the line, a square
    # ends it -- the same marks as the handles on the canvas)
    "line-start": ("M3 12a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0-5 0z", "M8 12h13"),
    "line-end": ("M3 12h13", "M16 9.5h5v5h-5z"),
    "bend-add": (
        "M3 19l7-10 11 5",
        "M8 9a2 2 0 1 0 4 0a2 2 0 1 0-4 0z",
        "M18 3v5M15.5 5.5h5",
    ),
    "bend-remove": (
        "M3 19l7-10 11 5",
        "M8 9a2 2 0 1 0 4 0a2 2 0 1 0-4 0z",
        "M15.5 5.5h5",
    ),
    # corner radius on a bend: a rounded corner and its radius; on
    # every bend: a path with two rounded corners
    "bend-radius": ("M4 20v-8a7 7 0 0 1 7-7h9", "M11 12L6.1 7.1"),
    "bend-radius-all": ("M3 19h5a3 3 0 0 0 3-3V8a3 3 0 0 1 3-3h7",),
    # a text frame with its leader (Add Leader)
    "leader": ("M3 4h9v6H3z", "M12 10l8 8", "M20 13v5h-5"),
    "fill": (
        "M5 5h14v14H5z",
        "M5 12l7-7M5 19L19 5M12 19l7-7",
    ),
    "sliders": (
        "M4 6h3M11 6h9",
        "M9 4a2 2 0 1 0 0 4a2 2 0 1 0 0-4z",
        "M4 12h9M17 12h3",
        "M15 10a2 2 0 1 0 0 4a2 2 0 1 0 0-4z",
        "M4 18h1M9 18h11",
        "M7 16a2 2 0 1 0 0 4a2 2 0 1 0 0-4z",
    ),
    "select-all": (
        "M4 7V4h3M10 4h4M17 4h3v3M20 10v4M20 17v3h-3M14 20h-4M7 20H4v-3"
        "M4 14v-4",
    ),
    "brush": ("M5 4h12v5H5z", "M17 6.5h2v5h-7v3", "M10.5 14.5h3v6h-3z"),
    "arrow-up": ("M12 19V5M6 11l6-6 6 6",),
    "arrow-down": ("M12 5v14M6 13l6 6 6-6",),
    "command": (
        "M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6"
        "a3 3 0 1 0 3 3z",
    ),
}


def glyph_names() -> tuple[str, ...]:
    return tuple(_GLYPHS)


def _svg(name: str, color: QColor, stroke: float) -> QByteArray:
    paths = "".join(f'<path d="{d}"/>' for d in _GLYPHS[name])
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color.name()}" stroke-width="{stroke}" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f"{paths}</svg>"
    )
    return QByteArray(doc.encode("utf-8"))


def line_pixmap(
    name: str, color: QColor, size: int = 40, stroke: float = 1.6
) -> QPixmap:
    """Rasterize one glyph. `size` is in device pixels (render big and
    let QIcon downscale, as `views/icons.py` does)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    renderer = QSvgRenderer(_svg(name, color, stroke))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def line_icon(
    name: str, color: QColor, size: int = 40, stroke: float = 1.6
) -> QIcon:
    """QIcon for a glyph; unknown names raise KeyError (caught by tests)."""
    return QIcon(line_pixmap(name, color, size, stroke))
