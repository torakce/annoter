"""SelectionToolbar: floating contextual action bar near the selection.

Reworked after user feedback: color/stroke were removed (they live in
the top toolbar) and the pill now shows the actions SPECIFIC to what is
selected -- the ones that otherwise need a right-click or a trip to the
Properties dock:

    text / note / GD&T / dimension   Edit
    rectangle / cloud       Outline (straight/cloud), Fill toggle
    line / arrow            Ends menu, add bend
    polyline / polygon      Closed toggle
    multi-selection         Group / Ungroup, Align menu

Duplicate / Delete close every variant. The widget is rebuilt by
`set_context` on each selection change and stays dumb: it emits
semantic signals, MainWindow owns the behavior (and passes its own
align QActions for the Align menu).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QToolButton, QWidget

from annoter.model.styles import END_STYLE_LABELS, EndStyle, HandleRole
from annoter.views.icons import end_icon
from annoter.views.items.base import AnnotationItem
from annoter.views.items.dimension import DimensionAnnotationItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.note import StickyNoteItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, RectangleItem
from annoter.views.items.text import TextAnnotationItem

_ACCENT = "#1E88E5"
_FIELD_BORDER = "#9aa0a6"

_TOOLBAR_QSS = f"""
#SelectionToolbar {{
    background-color: palette(window);
    border: 1px solid {_ACCENT};
    border-radius: 6px;
}}
#SelectionToolbar QToolButton {{
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 6px;
}}
#SelectionToolbar QToolButton:hover {{
    border: 1px solid {_FIELD_BORDER};
}}
#SelectionToolbar QToolButton:checked {{
    border: 1px solid {_ACCENT};
}}
"""


class SelectionToolbar(QFrame):
    """Floating pill of context-sensitive actions. Parent it to the
    view's viewport; MainWindow drives visibility and position."""

    editClicked = Signal()
    outlinePicked = Signal(bool)  # True = cloud outline
    fillToggled = Signal(bool)
    endStylePicked = Signal(object, object)  # (HandleRole, EndStyle)
    addBendClicked = Signal()
    closedToggled = Signal(bool)
    groupClicked = Signal()
    ungroupClicked = Signal()
    duplicateClicked = Signal()
    deleteClicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("SelectionToolbar")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_TOOLBAR_QSS)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 3, 4, 3)
        self._lay.setSpacing(2)
        # The widget always tracks its layout's size (same proven fix as
        # the GD&T inline editor): Qt re-applies the fixed size on every
        # layout change, so the pill can never linger as a tiny square.
        self._lay.setSizeConstraint(QHBoxLayout.SizeConstraint.SetFixedSize)
        self._icon_color = QColor("#212121")
        self.hide()

    # ------------------------------------------------------------------
    # context
    # ------------------------------------------------------------------
    def set_context(
        self,
        items: list[AnnotationItem],
        align_actions: list[QAction] | None = None,
        has_group: bool = False,
        icon_color: QColor | None = None,
    ) -> None:
        """Rebuild the pill for the current selection (empty list hides)."""
        if icon_color is not None:
            self._icon_color = QColor(icon_color)
        self._clear()
        if not items:
            self.hide()
            return
        if len(items) > 1:
            self._build_multi(align_actions or [], has_group)
        else:
            self._build_single(items[0])
        self._add_common()
        # Force a synchronous relayout: adjustSize() alone on a hidden,
        # just-rebuilt widget can act on a stale sizeHint and leave the
        # pill as a tiny empty square until something else retriggers a
        # layout pass (the bug reported in Discussion #1).
        self._lay.invalidate()
        self._lay.activate()
        self.resize(self.sizeHint())

    def _clear(self) -> None:
        while self._lay.count():
            child = self._lay.takeAt(0)
            w = child.widget()
            if w is not None:
                # Detach immediately: deleteLater alone keeps the old
                # buttons visible (and findable) until the event loop
                # runs, so a rebuilt pill would briefly show both sets.
                w.setParent(None)
                w.deleteLater()

    def _button(
        self, text: str, tooltip: str, checkable: bool = False
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setCheckable(checkable)
        btn.setFocusPolicy(Qt.NoFocus)
        self._lay.addWidget(btn)
        return btn

    def _v_separator(self) -> None:
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        self._lay.addWidget(line)

    # ------------------------------------------------------------------
    # variants
    # ------------------------------------------------------------------
    def _build_multi(
        self, align_actions: list[QAction], has_group: bool
    ) -> None:
        group = self._button("Group", "Group selection (Ctrl+G)")
        group.clicked.connect(self.groupClicked)
        if has_group:
            ungroup = self._button("Ungroup", "Ungroup (Ctrl+Shift+G)")
            ungroup.clicked.connect(self.ungroupClicked)
        if align_actions:
            align = self._button("Align", "Align / distribute")
            align.setPopupMode(QToolButton.InstantPopup)
            menu = QMenu(align)
            menu.addActions(align_actions)
            align.setMenu(menu)

    def _build_single(self, item: AnnotationItem) -> None:
        if isinstance(
            item,
            (
                TextAnnotationItem,
                StickyNoteItem,
                GdtAnnotationItem,
                DimensionAnnotationItem,
            ),
        ):
            edit = self._button("Edit", "Edit content (double-click)")
            edit.clicked.connect(self.editClicked)
        if isinstance(item, (RectangleItem, CloudItem)):
            self._build_outline_button(item)
            fill = self._button("Fill", "Toggle fill", checkable=True)
            fill.setChecked(bool(item.fill_enabled()))
            fill.toggled.connect(self.fillToggled)
        if isinstance(item, LineItem):
            self._build_ends_button(item)
            bend = self._button("+ Bend", "Add a bend point (or right-click)")
            bend.clicked.connect(self.addBendClicked)
        if isinstance(item, (PolylineItem, PolygonItem)):
            closed = self._button(
                "Closed", "Open polyline <-> closed polygon", checkable=True
            )
            closed.setChecked(isinstance(item, PolygonItem))
            closed.toggled.connect(self.closedToggled)

    def _build_outline_button(self, item: AnnotationItem) -> None:
        btn = self._button("Outline", "Border shape: straight or cloud")
        btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn)
        for label, cloudy in (("Straight", False), ("Cloud", True)):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(cloudy == isinstance(item, CloudItem))
            act.triggered.connect(
                lambda _c=False, v=cloudy: self.outlinePicked.emit(v)
            )
        btn.setMenu(menu)

    def _build_ends_button(self, item: LineItem) -> None:
        btn = self._button("Ends", "Extremity shapes")
        btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn)
        for title, role in (("Start", HandleRole.P1), ("End", HandleRole.P2)):
            sub = menu.addMenu(title)
            current = EndStyle.NONE
            if isinstance(item, ArrowItem):
                current = (
                    item.start_end()
                    if role is HandleRole.P1
                    else item.end_end()
                )
            for style, label in END_STYLE_LABELS:
                act = sub.addAction(
                    end_icon(style, color=self._icon_color), label
                )
                act.setCheckable(True)
                act.setChecked(style is current)
                act.triggered.connect(
                    lambda _c=False, r=role, s=style: (
                        self.endStylePicked.emit(r, s)
                    )
                )
        btn.setMenu(menu)

    def _add_common(self) -> None:
        self._v_separator()
        dup = self._button("Duplicate", "Duplicate (Ctrl+D)")
        dup.clicked.connect(self.duplicateClicked)
        delete = self._button("Delete", "Delete (Del)")
        delete.clicked.connect(self.deleteClicked)
