"""ToolController: the single source of truth for current tool/color/stroke.

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


class ToolController(QObject):
    """Holds current Tool, color, stroke width. Emits Qt signals on change."""

    toolChanged = Signal(Tool)
    colorChanged = Signal(QColor)
    strokeChanged = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tool: Tool = Tool.SELECT
        self._color: QColor = QColor(DEFAULT_PALETTE[0])
        self._stroke: float = STROKE_WIDTHS[1]

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
