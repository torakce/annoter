"""ToolPalette: dockable widget exposing the drawing-tool choice.

Subscribes to a ToolController and pushes the user's choice back into
it. Color and stroke width are NOT here: those quick controls live in
the top toolbar (Discussion #1 follow-up -- one place per function).
All visible strings are English.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDockWidget,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.controllers.tools import Tool, ToolController
from annoter.views.icons import tool_icon


# Related tools are merged (Discussion #1, item 3): one button per
# family, the variant is switched afterwards in the Properties dock
# (rectangle <-> cloud via "Outline", polyline <-> polygon via "Closed
# shape"; plain lines are an arrow with both end styles set to None).
_TOOL_LABELS: list[tuple[Tool, str]] = [
    (Tool.SELECT, "Select"),
    (Tool.RECTANGLE, "Rectangle"),
    (Tool.ELLIPSE, "Ellipse"),
    (Tool.ARROW, "Line / Arrow"),
    (Tool.POLYLINE, "Polyline"),
    (Tool.TEXT, "Text"),
    (Tool.STICKY_NOTE, "Sticky note"),
    (Tool.STAMP, "Stamp"),
    (Tool.FREEHAND, "Freehand"),
    (Tool.GDT, "GD&T frame"),
    (Tool.DIMENSION, "Dimension"),
]


class ToolPalette(QDockWidget):
    """Drawing-tool palette dock."""

    def __init__(
        self, controller: ToolController, parent: QWidget | None = None
    ) -> None:
        super().__init__("Tools", parent)
        self.setObjectName("ToolPaletteDock")
        self._controller = controller

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_tools_section())
        layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.setWidget(scroll)

        controller.toolChanged.connect(self._sync_tool_buttons)
        self._sync_tool_buttons(controller.tool())

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------
    def _build_tools_section(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(QLabel("Tool"))
        grid = QGridLayout()
        grid.setSpacing(4)
        v.addLayout(grid)

        self._tool_group = QButtonGroup(box)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[Tool, QToolButton] = {}
        for idx, (tool, name) in enumerate(_TOOL_LABELS):
            btn = QToolButton()
            btn.setText(name)
            btn.setIcon(tool_icon(tool))
            btn.setIconSize(QSize(18, 18))
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            btn.setCheckable(True)
            btn.setMinimumWidth(96)
            btn.clicked.connect(
                lambda _checked=False, t=tool: self._controller.set_tool(t)
            )
            self._tool_group.addButton(btn)
            self._tool_buttons[tool] = btn
            grid.addWidget(btn, idx // 2, idx % 2)
        return box

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------
    def _sync_tool_buttons(self, tool: Tool) -> None:
        for t, btn in self._tool_buttons.items():
            btn.setChecked(t is tool)

    def set_icon_color(self, color: QColor) -> None:
        """Repaint the tool icons. Called by MainWindow on theme change."""
        for t, btn in self._tool_buttons.items():
            btn.setIcon(tool_icon(t, color=color))
