"""ToolPalette: dockable widget exposing tool / color / stroke choices.

Subscribes to a ToolController and pushes the user's choices back into
it. All visible strings are English.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.config import DEFAULT_PALETTE, STROKE_WIDTHS
from annoter.controllers.tools import Tool, ToolController
from annoter.views.icons import tool_icon


_TOOL_LABELS: list[tuple[Tool, str]] = [
    (Tool.SELECT, "Select"),
    (Tool.RECTANGLE, "Rectangle"),
    (Tool.ELLIPSE, "Ellipse"),
    (Tool.LINE, "Line"),
    (Tool.ARROW, "Arrow"),
    (Tool.TEXT, "Text"),
    (Tool.FREEHAND, "Freehand"),
]


def _color_swatch(color: QColor, size: int = 20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(0, 0, 0, 80))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return QIcon(pm)


class ToolPalette(QDockWidget):
    """Tool / color / stroke palette dock."""

    def __init__(
        self, controller: ToolController, parent: QWidget | None = None
    ) -> None:
        super().__init__("Tools", parent)
        self.setObjectName("ToolPaletteDock")
        self._controller = controller
        self._custom_color: QColor | None = None

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        layout.addWidget(self._build_tools_section())
        layout.addWidget(self._h_separator())
        layout.addWidget(self._build_color_section())
        layout.addWidget(self._h_separator())
        layout.addWidget(self._build_stroke_section())
        layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.setWidget(scroll)

        controller.toolChanged.connect(self._sync_tool_buttons)
        controller.colorChanged.connect(self._sync_color_buttons)
        controller.strokeChanged.connect(self._sync_stroke_buttons)
        self._sync_tool_buttons(controller.tool())
        self._sync_color_buttons(controller.color())
        self._sync_stroke_buttons(controller.stroke())

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------
    def _h_separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

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

    def _build_color_section(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(QLabel("Color"))

        row = QHBoxLayout()
        row.setSpacing(4)
        v.addLayout(row)

        self._color_buttons: list[tuple[QToolButton, QColor]] = []
        for hex_ in DEFAULT_PALETTE:
            color = QColor(hex_)
            btn = QToolButton()
            btn.setCheckable(True)
            btn.setIcon(_color_swatch(color))
            btn.setIconSize(QSize(20, 20))
            btn.setToolTip(color.name())
            btn.clicked.connect(
                lambda _checked=False, c=color: self._controller.set_color(c)
            )
            row.addWidget(btn)
            self._color_buttons.append((btn, color))

        self._custom_btn = QToolButton()
        self._custom_btn.setText("...")
        self._custom_btn.setCheckable(True)
        self._custom_btn.setToolTip("Custom color...")
        self._custom_btn.setMinimumWidth(28)
        self._custom_btn.clicked.connect(self._on_custom_color_clicked)
        row.addWidget(self._custom_btn)
        row.addStretch(1)
        return box

    def _build_stroke_section(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(QLabel("Stroke"))

        row = QHBoxLayout()
        row.setSpacing(4)
        v.addLayout(row)

        self._stroke_group = QButtonGroup(box)
        self._stroke_group.setExclusive(True)
        self._stroke_buttons: list[tuple[QPushButton, float]] = []
        for w in STROKE_WIDTHS:
            btn = QPushButton(f"{w:g} px")
            btn.setCheckable(True)
            btn.clicked.connect(
                lambda _checked=False, ww=w: self._controller.set_stroke(ww)
            )
            self._stroke_group.addButton(btn)
            row.addWidget(btn)
            self._stroke_buttons.append((btn, w))
        row.addStretch(1)
        return box

    # ------------------------------------------------------------------
    # custom color
    # ------------------------------------------------------------------
    def _on_custom_color_clicked(self) -> None:
        initial = (
            self._custom_color
            if self._custom_color is not None
            else self._controller.color()
        )
        color = QColorDialog.getColor(initial, self, "Pick a custom color")
        if color.isValid():
            self._custom_color = color
            self._custom_btn.setIcon(_color_swatch(color))
            self._custom_btn.setIconSize(QSize(20, 20))
            self._controller.set_color(color)
        else:
            self._sync_color_buttons(self._controller.color())

    # ------------------------------------------------------------------
    # sync
    # ------------------------------------------------------------------
    def _sync_tool_buttons(self, tool: Tool) -> None:
        for t, btn in self._tool_buttons.items():
            btn.setChecked(t is tool)

    def _sync_color_buttons(self, color: QColor) -> None:
        matched = False
        for btn, c in self._color_buttons:
            is_match = c == color
            btn.setChecked(is_match)
            matched = matched or is_match
        if matched:
            self._custom_btn.setChecked(False)
        else:
            self._custom_btn.setChecked(True)

    def _sync_stroke_buttons(self, width: float) -> None:
        for btn, w in self._stroke_buttons:
            btn.setChecked(abs(w - width) < 1e-6)
