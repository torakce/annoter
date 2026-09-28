"""Floating overlays drawn over the PDF view (UI redesign, Lot D).

Three small widgets parented to the view's viewport, like the
`EditToolbar`, each keeping itself anchored when the viewport is
resized:

- `ToolHintChip` (top center): the active tool's name and a one-line
  "how to use it" hint, so a first-time user never wonders what a click
  will do. Can be turned off from View > Show Tool Hints.
- `CanvasNavPill` (bottom center): page navigation (previous, editable
  page number, next) and zoom (out, preset menu, in, fit, 1:1, zoom to
  area, rotate). Replaces the status-bar page / zoom labels and the
  zoom buttons of the old toolbar.
- `CanvasToast` (bottom center, above the pill): transient messages
  ("Exported 3 files", Format Painter usage) that used to go to the
  status bar.
- `DraftBar` (top center, under the hint): Finish / Remove last point /
  Cancel while a polyline or polygon is being drawn (2026-09-28: the
  keyboard ways to end one were not discoverable).

The widgets stay dumb: the pill triggers the QActions it is given and
emits `pageRequested` / `zoomRequested`; MainWindow owns the behavior.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QIntValidator
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QWidget,
)

from annoter.views.line_icons import line_icon

from annoter.controllers.tools import LineKind, Tool

_MARGIN = 14

# Tool -> (name, hint). The Line / arrow tool's text depends on the
# line kind (see LINE_HINTS); the Stamp tool names the chosen stamp.
TOOL_HINTS: dict[Tool, tuple[str, str]] = {
    Tool.SELECT: (
        "Select",
        "Click to select · Shift+click to add · "
        "Alt+click on a pile to choose · Drag to select an area",
    ),
    Tool.RECTANGLE: (
        "Rectangle",
        "Drag to draw · Shift keeps a square · "
        "Switch to a cloud outline afterwards",
    ),
    Tool.ELLIPSE: ("Ellipse", "Drag to draw · Shift keeps a circle"),
    Tool.POLYLINE: (
        "Polyline",
        "Click to add points · Double-click, Enter or Esc to finish "
        "· Backspace removes the last point",
    ),
    Tool.POLYGON: (
        "Polygon",
        "Click to add points · Click the first point, double-click, "
        "Enter or Esc to close it",
    ),
    Tool.FREEHAND: (
        "Freehand",
        "Draw freely · The tool stays active · Esc to stop",
    ),
    Tool.TEXT: ("Text", "Click to place a text box, then type"),
    Tool.STICKY_NOTE: ("Sticky note", "Click to pin a note, then type"),
    Tool.STAMP: ("Stamp", "Click to place"),
    Tool.GDT: ("GD&T frame", "Click to place a feature control frame"),
    Tool.DIMENSION: (
        "Dimension",
        "Click two points, then click where the dimension line goes "
        "· Shift: horizontal / vertical · Esc cancels",
    ),
    Tool.FORMAT_PAINTER: (
        "Format Painter",
        "Click annotations to apply the copied style · Esc to stop",
    ),
}

LINE_HINTS: dict[LineKind, tuple[str, str]] = {
    LineKind.LINE: (
        "Line",
        "Drag to draw a plain line · Shift snaps the angle",
    ),
    LineKind.ARROW: (
        "Arrow",
        "Drag from the tail to the head · Shift snaps the angle",
    ),
    LineKind.DOUBLE: (
        "Double arrow",
        "Drag to draw · Heads at both ends · "
        "Shift snaps the angle",
    ),
}

# Zoom presets offered by the pill's zoom menu (factor, label).
ZOOM_PRESETS: list[tuple[float, str]] = [
    (0.25, "25 %"),
    (0.5, "50 %"),
    (0.75, "75 %"),
    (1.0, "100 %"),
    (1.5, "150 %"),
    (2.0, "200 %"),
    (4.0, "400 %"),
]


def hint_for(
    tool: Tool, line_kind: LineKind, stamp_text: str = ""
) -> tuple[str, str] | None:
    """(name, hint) for the chip, or None when the tool has no hint."""
    if tool is Tool.ARROW:
        return LINE_HINTS[line_kind]
    if tool is Tool.STAMP and stamp_text:
        return ("Stamp", f"Click to place “{stamp_text}”")
    return TOOL_HINTS.get(tool)


class _Anchor(Enum):
    TOP_CENTER = "top_center"
    BOTTOM_CENTER = "bottom_center"


class _AnchoredOverlay(QFrame):
    """A frame that keeps itself at an anchor of its parent (the view's
    viewport), re-placing itself whenever the parent is resized."""

    def __init__(
        self, parent: QWidget, anchor: _Anchor, lift: int = 0
    ) -> None:
        super().__init__(parent)
        self._anchor = anchor
        self._lift = lift  # extra distance from the anchored edge
        self.setAttribute(Qt.WA_StyledBackground, True)
        parent.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.reposition()
        return False

    def reposition(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        self.adjustSize()
        w, h = self.width(), self.height()
        x = (host.width() - w) // 2
        if self._anchor is _Anchor.TOP_CENTER:
            y = _MARGIN + self._lift
        else:
            y = host.height() - h - _MARGIN - self._lift
        self.move(max(0, x), max(0, y))


class ToolHintChip(_AnchoredOverlay):
    """Top-center chip: bold tool name + muted usage hint."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, _Anchor.TOP_CENTER)
        self.setObjectName("ToolHint")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 14, 6)
        row.setSpacing(8)
        self.name_label = QLabel("", self)
        self.name_label.setObjectName("ToolHintName")
        self.hint_label = QLabel("", self)
        self.hint_label.setObjectName("ToolHintText")
        row.addWidget(self.name_label)
        row.addWidget(self.hint_label)
        self._enabled = True
        self._has_content = False
        self._full_hint = ""
        self.hide()

    def set_hint(self, hint: tuple[str, str] | None) -> None:
        self._has_content = hint is not None
        if hint is not None:
            self.name_label.setText(hint[0])
            self._full_hint = hint[1]
            self.hint_label.setText(hint[1])
        self._refresh_visibility()

    def reposition(self) -> None:
        # On a narrow canvas the hint is cut short ("...") rather than
        # pushing the chip past the edges.
        host = self.parentWidget()
        if host is not None and self._full_hint:
            # Measure with the themed fonts (QSS applies them on polish).
            self.name_label.ensurePolished()
            self.hint_label.ensurePolished()
            margins = self.layout().contentsMargins()
            room = (
                host.width()
                - 2 * _MARGIN
                - margins.left()
                - margins.right()
                - self.layout().spacing()
                - self.name_label.sizeHint().width()
            )
            fm = QFontMetrics(self.hint_label.font())
            self.hint_label.setText(
                fm.elidedText(self._full_hint, Qt.ElideRight, max(0, room))
            )
        super().reposition()

    def set_hints_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self._refresh_visibility()

    def text(self) -> str:
        return f"{self.name_label.text()}: {self._full_hint}"

    def _refresh_visibility(self) -> None:
        visible = self._enabled and self._has_content
        if visible:
            self.reposition()
            self.show()
            self.raise_()
        else:
            self.hide()


class _PillButton(QToolButton):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)


def _divider(parent: QWidget) -> QFrame:
    d = QFrame(parent)
    d.setObjectName("PillDivider")
    d.setFixedSize(1, 20)
    return d


class CanvasNavPill(_AnchoredOverlay):
    """Bottom-center page + zoom controls."""

    pageRequested = Signal(int)  # 0-based page index
    zoomRequested = Signal(float)  # absolute factor, 1.0 = 100 %

    def __init__(
        self,
        parent: QWidget,
        *,
        prev_page: QAction,
        next_page: QAction,
        zoom_out: QAction,
        zoom_in: QAction,
        zoom_fit: QAction,
        zoom_actual: QAction,
        zoom_window: QAction,
        rotate: QAction,
    ) -> None:
        super().__init__(parent, _Anchor.BOTTOM_CENTER)
        self.setObjectName("CanvasPill")
        self._page_count = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(2)

        self.prev_button = self._action_button(prev_page)
        row.addWidget(self.prev_button)
        self.page_edit = QLineEdit(self)
        self.page_edit.setObjectName("PillPageField")
        self.page_edit.setAlignment(Qt.AlignCenter)
        self.page_edit.setFixedWidth(40)
        self.page_edit.setToolTip("Type a page number and press Enter")
        self.page_edit.setAccessibleName("Page number")
        self._validator = QIntValidator(1, 1, self.page_edit)
        self.page_edit.setValidator(self._validator)
        self.page_edit.returnPressed.connect(self._on_page_typed)
        row.addWidget(self.page_edit)
        self.page_total = QLabel("of 0", self)
        self.page_total.setObjectName("PillMuted")
        row.addWidget(self.page_total)
        self.next_button = self._action_button(next_page)
        row.addWidget(self.next_button)

        row.addWidget(_divider(self))
        self.zoom_out_button = self._action_button(zoom_out)
        row.addWidget(self.zoom_out_button)
        self.zoom_button = _PillButton(self)
        self.zoom_button.setObjectName("PillZoom")
        self.zoom_button.setToolTip("Zoom presets")
        self.zoom_button.setPopupMode(QToolButton.InstantPopup)
        self.zoom_button.setText("100 %")
        menu = QMenu(self.zoom_button)
        for factor, label in ZOOM_PRESETS:
            act = menu.addAction(label)
            act.triggered.connect(
                lambda _c=False, f=factor: self.zoomRequested.emit(f)
            )
        menu.addSeparator()
        menu.addAction(zoom_fit)
        menu.addAction(zoom_actual)
        self.zoom_button.setMenu(menu)
        row.addWidget(self.zoom_button)
        self.zoom_in_button = self._action_button(zoom_in)
        row.addWidget(self.zoom_in_button)

        row.addWidget(_divider(self))
        self.fit_button = self._action_button(zoom_fit, text="Fit")
        row.addWidget(self.fit_button)
        self.actual_button = self._action_button(zoom_actual, text="1:1")
        row.addWidget(self.actual_button)
        self.area_button = self._action_button(zoom_window)
        row.addWidget(self.area_button)

        row.addWidget(_divider(self))
        self.rotate_button = self._action_button(rotate)
        row.addWidget(self.rotate_button)

    # ------------------------------------------------------------------
    def _action_button(
        self, action: QAction, text: str | None = None
    ) -> _PillButton:
        """A button mirroring `action` (enabled state, tooltip, icon).

        Not `setDefaultAction`: that would also copy the action's menu
        text ("Zoom &In") onto text-style buttons like Fit and 1:1.
        """
        b = _PillButton(self)
        b.setAccessibleName(action.text().replace("&", ""))

        def sync() -> None:
            b.setEnabled(action.isEnabled())
            b.setToolTip(action.toolTip())
            if text is None:
                b.setIcon(action.icon())
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)
            else:
                b.setIcon(action.icon())
                b.setText(text)
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        sync()
        action.changed.connect(sync)
        b.clicked.connect(action.trigger)
        return b

    def set_page(self, index: int, count: int) -> None:
        """Show page `index` (0-based) of `count`."""
        self._page_count = max(0, int(count))
        self._validator.setRange(1, max(1, self._page_count))
        self.page_edit.setText(str(index + 1) if count else "")
        self.page_total.setText(f"of {self._page_count}")
        self.page_edit.setEnabled(self._page_count > 1)
        self.reposition()

    def set_zoom(self, factor: float) -> None:
        self.zoom_button.setText(f"{factor * 100:.0f} %")
        self.reposition()

    def _on_page_typed(self) -> None:
        text = self.page_edit.text().strip()
        if not text.isdigit():
            return
        n = int(text)
        if 1 <= n <= self._page_count:
            self.pageRequested.emit(n - 1)
        self.page_edit.clearFocus()


# Height of the nav pill plus a gap: the toast sits just above it.
_TOAST_LIFT = 52


class CanvasToast(_AnchoredOverlay):
    """Transient message above the nav pill (replaces the status bar)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, _Anchor.BOTTOM_CENTER, lift=_TOAST_LIFT)
        self.setObjectName("CanvasToast")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 12, 7)
        self.label = QLabel("", self)
        self.label.setObjectName("CanvasToastText")
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(420)
        row.addWidget(self.label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, msec: int = 4000) -> None:
        self.label.setText(text)
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(max(500, int(msec)))

    def text(self) -> str:
        return self.label.text()


# Below the tool hint chip.
_DRAFT_BAR_LIFT = 44


class DraftBar(_AnchoredOverlay):
    """Top-center bar shown while a polyline / polygon is drawn.

    The keyboard and mouse ways to end one (double-click, Enter, Esc,
    right-click) stay; this makes them visible and clickable. Dumb:
    MainWindow feeds `set_point_count` from the scene and connects the
    three signals.
    """

    finishClicked = Signal()
    removePointClicked = Signal()
    cancelClicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent, _Anchor.TOP_CENTER, lift=_DRAFT_BAR_LIFT)
        self.setObjectName("DraftBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(4)
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("PillMuted")
        row.addWidget(self.count_label)
        self.remove_button = self._button(
            "Remove last point", "undo", "Remove the last point (Backspace)"
        )
        self.remove_button.clicked.connect(self.removePointClicked)
        row.addWidget(self.remove_button)
        self.cancel_button = self._button(
            "Cancel", "close-doc", "Drop this shape"
        )
        self.cancel_button.clicked.connect(self.cancelClicked)
        row.addWidget(self.cancel_button)
        self.finish_button = self._button(
            "Finish", "check", "Finish the shape (Enter, Esc, double-click "
            "or right-click)",
        )
        self.finish_button.setObjectName("DraftFinish")
        self.finish_button.clicked.connect(self.finishClicked)
        row.addWidget(self.finish_button)
        self._glyphs = {
            self.remove_button: "undo",
            self.cancel_button: "close-doc",
            self.finish_button: "check",
        }
        self.hide()

    def _button(self, text: str, glyph: str, tip: str) -> QToolButton:
        btn = _PillButton(self)
        btn.setText(text)
        btn.setToolTip(tip)
        btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        return btn

    def set_icon_color(self, color, on_accent=None) -> None:  # noqa: ANN001
        """Glyph color; `on_accent` for the Finish button's (it sits on
        the accent fill)."""
        for btn, glyph in self._glyphs.items():
            c = on_accent if btn is self.finish_button and on_accent else color
            btn.setIcon(line_icon(glyph, c))

    def set_point_count(self, count: int, minimum: int) -> None:
        """Show for a draft of `count` points (hide at 0); Finish needs
        `minimum` of them."""
        if count <= 0:
            self.hide()
            return
        self.count_label.setText(
            f"{count} point" + ("" if count == 1 else "s")
        )
        self.finish_button.setEnabled(count >= minimum)
        self.reposition()
        self.show()
        self.raise_()
