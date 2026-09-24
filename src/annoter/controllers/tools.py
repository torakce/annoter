"""ToolController: the single source of truth for current tool/color/stroke.

It also holds the per-tool variants picked before drawing (UI redesign,
Lot C): which kind of line the Line / arrow tool draws, and which stamp
the Stamp tool places.

Views subscribe to its signals; views never read each other directly.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from annoter.config import DEFAULT_PALETTE, STROKE_WIDTHS


class Tool(Enum):
    """Available drawing tools."""

    SELECT = auto()
    RECTANGLE = auto()
    ELLIPSE = auto()
    CLOUD = auto()
    LINE = auto()
    ARROW = auto()
    POLYLINE = auto()
    POLYGON = auto()
    TEXT = auto()
    CALLOUT = auto()
    STICKY_NOTE = auto()
    STAMP = auto()
    FREEHAND = auto()
    GDT = auto()
    DIMENSION = auto()
    # Action mode (not shown in the drawing-tool palette): click an
    # annotation to copy a previously captured style onto it.
    FORMAT_PAINTER = auto()


class LineKind(Enum):
    """What the Line / arrow tool draws. Every kind is an ArrowItem; the
    kind only picks its end styles (a plain line has none), so the ends
    stay editable afterwards like before."""

    LINE = "line"
    ARROW = "arrow"
    DOUBLE = "double"


# Stamp placed by default (same as StampItem's own default).
DEFAULT_STAMP_TEXT = "APPROVED"
DEFAULT_STAMP_COLOR = "#2E7D32"


class ToolController(QObject):
    """Holds current Tool, color, stroke width. Emits Qt signals on change."""

    toolChanged = Signal(Tool)
    colorChanged = Signal(QColor)
    strokeChanged = Signal(float)
    lineKindChanged = Signal(object)  # LineKind
    stampPresetChanged = Signal(str, QColor)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tool: Tool = Tool.SELECT
        self._color: QColor = QColor(DEFAULT_PALETTE[0])
        self._stroke: float = STROKE_WIDTHS[1]
        self._line_kind: LineKind = LineKind.ARROW
        self._stamp_text: str = DEFAULT_STAMP_TEXT
        self._stamp_color: QColor = QColor(DEFAULT_STAMP_COLOR)

    # ------------------------------------------------------------------
    # tool
    # ------------------------------------------------------------------
    def tool(self) -> Tool:
        return self._tool

    def set_tool(self, tool: Tool) -> None:
        if tool is self._tool:
            return
        self._tool = tool
        self.toolChanged.emit(tool)

    # ------------------------------------------------------------------
    # color
    # ------------------------------------------------------------------
    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        c = QColor(color)
        if c == self._color:
            return
        self._color = c
        self.colorChanged.emit(QColor(c))

    # ------------------------------------------------------------------
    # stroke
    # ------------------------------------------------------------------
    def stroke(self) -> float:
        return self._stroke

    def set_stroke(self, width: float) -> None:
        w = float(width)
        if w == self._stroke:
            return
        self._stroke = w
        self.strokeChanged.emit(w)

    # ------------------------------------------------------------------
    # line kind (Line / arrow tool)
    # ------------------------------------------------------------------
    def line_kind(self) -> LineKind:
        return self._line_kind

    def set_line_kind(self, kind: LineKind) -> None:
        if kind is self._line_kind:
            return
        self._line_kind = kind
        self.lineKindChanged.emit(kind)

    # ------------------------------------------------------------------
    # stamp preset (Stamp tool)
    # ------------------------------------------------------------------
    def stamp_preset(self) -> tuple[str, QColor]:
        return self._stamp_text, QColor(self._stamp_color)

    def set_stamp_preset(self, text: str, color: QColor) -> None:
        t, c = str(text), QColor(color)
        if t == self._stamp_text and c == self._stamp_color:
            return
        self._stamp_text, self._stamp_color = t, c
        self.stampPresetChanged.emit(t, QColor(c))
