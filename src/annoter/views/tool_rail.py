"""ToolRail: the vertical, icon-only tool bar of the redesigned UI (Lot C).

Replaces the `ToolPalette` dock (a two-column grid of text buttons). It
is still the single place to pick a drawing tool and still only talks
to the `ToolController`: buttons push `set_tool`, the controller's
signals check the right button. Tool names live in tooltips.

Tools with variants (Line / arrow, Stamp) show a small corner mark and,
while they are the active tool, a `ToolFlyout` next to the rail lets
the user choose the variant *before* drawing: plain line, arrow or
double arrow; which stamp to place. The flyout does not grab input, so
the user can draw straight away with the current choice.

Deliberately no single-letter shortcuts (user decision, Lot C): typing
a letter on a selected shape keeps starting its label, as before. Tools
remain reachable from the keyboard through Ctrl+K.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.controllers.tools import LineKind, Tool, ToolController
from annoter.views.line_icons import line_pixmap

# Rail content, top to bottom; each inner list is a visual group.
TOOL_GROUPS: list[list[tuple[Tool, str, str]]] = [
    [(Tool.SELECT, "Select", "select")],
    [
        (Tool.RECTANGLE, "Rectangle / cloud", "rectangle"),
        (Tool.ELLIPSE, "Ellipse", "ellipse"),
        (Tool.ARROW, "Line / arrow", "arrow"),
        (Tool.POLYLINE, "Polyline / polygon", "polyline"),
        (Tool.FREEHAND, "Freehand", "freehand"),
    ],
    [
        (Tool.TEXT, "Text", "text"),
        (Tool.STICKY_NOTE, "Sticky note", "note"),
        (Tool.STAMP, "Stamp", "stamp"),
    ],
    [
        (Tool.GDT, "GD&T frame", "gdt"),
        (Tool.DIMENSION, "Dimension", "dimension"),
    ],
]

# Flat (tool, label) list, in rail order: the command palette lists the
# same tools with the same names.
TOOL_LABELS: list[tuple[Tool, str]] = [
    (tool, label) for group in TOOL_GROUPS for tool, label, _g in group
]

# Tools whose flyout offers variants.
VARIANT_TOOLS = (Tool.ARROW, Tool.STAMP)

LINE_KIND_OPTIONS: list[tuple[LineKind, str, str, str]] = [
    (LineKind.LINE, "Line", "No arrowhead", "line"),
    (LineKind.ARROW, "Arrow", "Head at the end you release", "arrow"),
    (LineKind.DOUBLE, "Double arrow", "Heads at both ends", "double-arrow"),
]

LINE_KIND_GLYPH = {kind: glyph for kind, _t, _d, glyph in LINE_KIND_OPTIONS}

RAIL_BUTTON = 44
RAIL_ICON = 22


class _RailButton(QToolButton):
    """Checkable icon button; tools with variants get a corner mark."""

    def __init__(self, has_variants: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self._has_variants = has_variants
        self._mark_color = QColor("#8A8984")
        self.setCheckable(True)
        self.setAutoRaise(True)
        self.setFixedSize(RAIL_BUTTON, RAIL_BUTTON)
        self.setIconSize(QSize(RAIL_ICON, RAIL_ICON))
        self.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.setFocusPolicy(Qt.NoFocus)

    def set_mark_color(self, color: QColor) -> None:
        self._mark_color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if not self._has_variants:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self._mark_color)
        r = self.rect()
        tri = QPainterPath()
        tri.moveTo(QPointF(r.right() - 4, r.top() + 4))
        tri.lineTo(QPointF(r.right() - 4, r.top() + 10))
        tri.lineTo(QPointF(r.right() - 10, r.top() + 4))
        tri.closeSubpath()
        p.drawPath(tri)
        p.end()


def _tool_icon(glyph: str, idle: QColor, active: QColor) -> QIcon:
    """Idle glyph for Off, accent glyph for On (checked)."""
    icon = QIcon()
    icon.addPixmap(line_pixmap(glyph, idle, 44), QIcon.Normal, QIcon.Off)
    icon.addPixmap(line_pixmap(glyph, active, 44), QIcon.Normal, QIcon.On)
    icon.addPixmap(line_pixmap(glyph, active, 44), QIcon.Active, QIcon.On)
    return icon


class ToolRail(QToolBar):
    """Vertical tool bar bound to a ToolController."""

    def __init__(
        self, controller: ToolController, parent: QWidget | None = None
    ) -> None:
        super().__init__("Tools", parent)
        self.setObjectName("ToolRail")
        self.setOrientation(Qt.Vertical)
        self.setMovable(False)
        self.setFloatable(False)
        self.setIconSize(QSize(RAIL_ICON, RAIL_ICON))
        self._controller = controller
        self._idle = QColor("#3A3B3E")
        self._active = QColor("#2447B0")

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[Tool, _RailButton] = {}
        self._glyphs: dict[Tool, str] = {}
        for i, group in enumerate(TOOL_GROUPS):
            if i:
                self.addSeparator()
            for tool, label, glyph in group:
                btn = _RailButton(tool in VARIANT_TOOLS, self)
                btn.setToolTip(label)
                btn.setAccessibleName(label)
                btn.clicked.connect(
                    lambda _c=False, t=tool: self._controller.set_tool(t)
                )
                self._group.addButton(btn)
                self._buttons[tool] = btn
                self._glyphs[tool] = glyph
                self.addWidget(btn)

        controller.toolChanged.connect(self._sync_checked)
        controller.lineKindChanged.connect(self._on_line_kind)
        self._glyphs[Tool.ARROW] = LINE_KIND_GLYPH[controller.line_kind()]
        self.set_colors(self._idle, self._active)
        self._sync_checked(controller.tool())

    # ------------------------------------------------------------------
    def button(self, tool: Tool) -> QToolButton | None:
        return self._buttons.get(tool)

    def tools(self) -> list[Tool]:
        return list(self._buttons)

    def _sync_checked(self, tool: Tool) -> None:
        btn = self._buttons.get(tool)
        if btn is not None:
            btn.setChecked(True)
        else:
            # Action modes (Format Painter) have no rail button: clear
            # the exclusive group so no tool looks active.
            self._group.setExclusive(False)
            for b in self._buttons.values():
                b.setChecked(False)
            self._group.setExclusive(True)

    def _on_line_kind(self, kind: LineKind) -> None:
        # The Line / arrow button shows what it will draw.
        self._glyphs[Tool.ARROW] = LINE_KIND_GLYPH[kind]
        self._repaint_icon(Tool.ARROW)

    def set_colors(
        self, idle: QColor, active: QColor, mark: QColor | None = None
    ) -> None:
        """Glyphs are pre-rasterized: repaint them on theme change."""
        self._idle, self._active = QColor(idle), QColor(active)
        for tool in self._buttons:
            self._repaint_icon(tool)
            if mark is not None:
                self._buttons[tool].set_mark_color(mark)

    def _repaint_icon(self, tool: Tool) -> None:
        btn = self._buttons.get(tool)
        if btn is not None:
            btn.setIcon(_tool_icon(self._glyphs[tool], self._idle, self._active))


# ----------------------------------------------------------------------
# flyout
# ----------------------------------------------------------------------
def stamp_chip(text: str, color: QColor, height: int = 22) -> QPixmap:
    """A small outlined label in the stamp's color, for the flyout."""
    dpr = 2.0
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(11 * dpr))
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.2 * dpr)
    fm = QFontMetricsF(font)
    w = fm.horizontalAdvance(text) + 16 * dpr
    h = height * dpr
    pm = QPixmap(int(w + 2), int(h))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(color, 1.5 * dpr))
    p.drawRoundedRect(QRectF(1, 1, w - 1, h - 2), 4 * dpr, 4 * dpr)
    p.setFont(font)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, text)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


class _OptionButton(QPushButton):
    """One flyout choice: icon, title, optional description, check."""

    def __init__(
        self,
        title: str,
        description: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("FlyoutOption")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 10, 6)
        row.setSpacing(10)
        self.icon_label = QLabel(self)
        self.icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self.icon_label)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("FlyoutOptionTitle")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        texts.addWidget(self.title_label)
        if description:
            desc = QLabel(description, self)
            desc.setObjectName("FlyoutOptionDesc")
            desc.setAttribute(Qt.WA_TransparentForMouseEvents)
            texts.addWidget(desc)
        row.addLayout(texts, 1)
        self.check_label = QLabel(self)
        self.check_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.check_label.setFixedWidth(16)
        row.addWidget(self.check_label)
        self.setMinimumHeight(40 if description else 34)

    def sizeHint(self) -> QSize:
        return self.layout().sizeHint()


class ToolFlyout(QFrame):
    """Variant chooser shown next to the rail while a variant tool is
    active. Dumb like the other floating bars: it emits the user's
    choice and MainWindow forwards it to the ToolController."""

    lineKindPicked = Signal(object)  # LineKind
    stampPicked = Signal(str, QColor)
    customStampRequested = Signal()

    def __init__(
        self,
        stamp_presets: list[tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToolFlyout")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._presets = [(t, QColor(c)) for t, c in stamp_presets]
        self._tool: Tool | None = None
        self._idle = QColor("#3A3B3E")
        self._active = QColor("#2447B0")

        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(2)
        self._title = QLabel("", self)
        self._title.setObjectName("FlyoutTitle")
        self._title.setContentsMargins(8, 4, 8, 4)
        col.addWidget(self._title)

        # Line / arrow page.
        self._line_page = QWidget(self)
        lv = QVBoxLayout(self._line_page)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(2)
        self._line_buttons: dict[LineKind, _OptionButton] = {}
        for kind, title, desc, _glyph in LINE_KIND_OPTIONS:
            b = _OptionButton(title, desc, self._line_page)
            b.clicked.connect(
                lambda _c=False, k=kind: self.lineKindPicked.emit(k)
            )
            self._line_buttons[kind] = b
            lv.addWidget(b)
        col.addWidget(self._line_page)

        # Stamp page.
        self._stamp_page = QWidget(self)
        sv = QVBoxLayout(self._stamp_page)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(2)
        self._stamp_buttons: list[_OptionButton] = []
        for text, color in self._presets:
            b = _OptionButton("", "", self._stamp_page)
            b.setAccessibleName(text)
            b.title_label.hide()
            b.icon_label.setPixmap(stamp_chip(text, color))
            b.clicked.connect(
                lambda _c=False, t=text, c=color: self.stampPicked.emit(t, c)
            )
            self._stamp_buttons.append(b)
            sv.addWidget(b)
        self._custom_button = _OptionButton(
            "Custom stamp...", "", self._stamp_page
        )
        self._custom_button.clicked.connect(self.customStampRequested)
        sv.addWidget(self._custom_button)
        col.addWidget(self._stamp_page)

        self.setFixedWidth(250)
        self.set_colors(self._idle, self._active)
        self.hide()

    # ------------------------------------------------------------------
    def tool(self) -> Tool | None:
        return self._tool

    def show_for(
        self,
        tool: Tool,
        line_kind: LineKind,
        stamp: tuple[str, QColor],
        anchor: QWidget,
    ) -> None:
        """Fill for `tool` and show it to the right of `anchor`."""
        self._tool = tool
        is_line = tool is Tool.ARROW
        self._line_page.setVisible(is_line)
        self._stamp_page.setVisible(not is_line)
        self._title.setText(
            "WHAT DO YOU WANT TO DRAW?" if is_line else "CHOOSE A STAMP"
        )
        if is_line:
            self.sync_line_kind(line_kind)
        else:
            self.sync_stamp(*stamp)
        self.adjustSize()
        host = self.parentWidget()
        if host is not None:
            top_right = anchor.mapTo(host, QPoint(anchor.width(), 0))
            y = max(4, top_right.y() - 6)
            y = min(y, max(4, host.height() - self.height() - 4))
            self.move(top_right.x() + 8, y)
        self.show()
        self.raise_()

    def dismiss(self) -> None:
        self._tool = None
        self.hide()

    def sync_line_kind(self, kind: LineKind) -> None:
        for k, b in self._line_buttons.items():
            b.setChecked(k is kind)
            self._set_check(b, k is kind)

    def sync_stamp(self, text: str, color: QColor) -> None:
        matched = False
        for (t, _c), b in zip(self._presets, self._stamp_buttons, strict=True):
            on = t == text
            matched = matched or on
            b.setChecked(on)
            self._set_check(b, on)
        custom = not matched
        self._custom_button.setChecked(custom)
        self._set_check(self._custom_button, custom)
        self._custom_button.title_label.setText(
            f"Custom: {text}" if custom else "Custom stamp..."
        )

    def set_colors(self, idle: QColor, active: QColor) -> None:
        self._idle, self._active = QColor(idle), QColor(active)
        for kind, _t, _d, glyph in LINE_KIND_OPTIONS:
            b = self._line_buttons[kind]
            pm = line_pixmap(glyph, self._idle, 40)
            pm.setDevicePixelRatio(2.0)
            b.icon_label.setPixmap(pm)
            self._set_check(b, b.isChecked())
        custom_icon = line_pixmap("plus", self._idle, 32)
        custom_icon.setDevicePixelRatio(2.0)
        self._custom_button.icon_label.setPixmap(custom_icon)
        for b in self._stamp_buttons + [self._custom_button]:
            self._set_check(b, b.isChecked())

    def _set_check(self, button: _OptionButton, on: bool) -> None:
        if on:
            pm = line_pixmap("check", self._active, 32)
            pm.setDevicePixelRatio(2.0)
            button.check_label.setPixmap(pm)
        else:
            button.check_label.clear()
