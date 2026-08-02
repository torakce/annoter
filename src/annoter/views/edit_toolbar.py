"""EditToolbar: floating contextual bar over the item being EDITED.

`SelectionToolbar` is the pill for what is *selected*; this is the pill
for what is being *edited*. It pops up above a text annotation the
moment its inline edit session starts and carries the actions that
belong to authoring content rather than to restyling a shape: font
size, bold / italic, alignment, outline, and the two drawing-specific
insertions -- a symbol at the caret and an inline tolerance run.

That is the point of the widget: placing a dimension or a tolerance is
no longer a tool of its own, it is something you do while writing the
text that carries it.

Architecture mirrors `SelectionToolbar` exactly -- the widget stays
dumb, emits semantic signals and lets MainWindow own every behaviour
(including the undo commands). `set_context(item)` rebuilds it for the
item's type; only text items are handled today and the branch is laid
out so other kinds can plug in later.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from annoter.model.styles import TEXT_BORDER_LABELS, TextAlign
from annoter.model.tolerance import (
    MODE_NAMES,
    PLUS_MINUS,
    Tolerance,
    ToleranceMode,
)
from annoter.views.icons import align_icon
from annoter.views.items.text import TextAnnotationItem

_ACCENT = "#1E88E5"
_FIELD_BORDER = "#9aa0a6"

_TOOLBAR_QSS = f"""
#EditToolbar {{
    background-color: palette(window);
    border: 1px solid {_ACCENT};
    border-radius: 6px;
}}
#EditToolbar QToolButton {{
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 6px;
}}
#EditToolbar QToolButton:hover {{
    border: 1px solid {_FIELD_BORDER};
}}
#EditToolbar QToolButton:checked {{
    border: 1px solid {_ACCENT};
}}
"""

# Same idiom as STROKE_LADDER in `stroke_spin`: fine steps where
# precision matters, coarser jumps once the values get large.
FONT_SIZE_LADDER: tuple[int, ...] = (
    6, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72,
)

_ALIGN_LABELS: list[tuple[TextAlign, str]] = [
    (TextAlign.LEFT, "Left"),
    (TextAlign.CENTER, "Center"),
    (TextAlign.RIGHT, "Right"),
]

# Symbols that live in WinAnsi, the encoding a native PDF FreeText
# string uses: these round-trip as real text, searchable and editable in
# Acrobat/Foxit with no appearance stream. Keep them first and grouped
# so the common picks stay on the cheap save path.
_WINANSI_SYMBOLS: list[tuple[str, str]] = [
    ("Ø", "Diameter (Latin capital O with stroke)"),
    ("°", "Degree"),
    ("±", "Plus-minus"),
    ("×", "Multiplication sign"),
    ("µ", "Micro"),
    ("R", "Radius"),
    ("SØ", "Spherical diameter"),
    ("SR", "Spherical radius"),
]

# Symbols OUTSIDE WinAnsi. They are the typographically correct ISO
# glyphs, but they cannot be written as a plain FreeText string, so
# using one forces the annotation onto the rasterized-appearance save
# path (same trade-off as a stacked bilateral tolerance run).
_WIDE_SYMBOLS: list[tuple[str, str]] = [
    ("⌀", "Diameter sign (ISO glyph, not WinAnsi)"),
    ("□", "Square (not WinAnsi)"),
    ("⌒", "Arc length (not WinAnsi)"),
]


def _next_ladder_up(value: int) -> int:
    for x in FONT_SIZE_LADDER:
        if x > value:
            return x
    return FONT_SIZE_LADDER[-1]


def _next_ladder_down(value: int) -> int:
    for x in reversed(FONT_SIZE_LADDER):
        if x < value:
            return x
    return FONT_SIZE_LADDER[0]


class _ToleranceForm(QWidget):
    """Tiny inline form for one tolerance mode, hosted in a QMenu.

    Symmetric asks for a single magnitude, bilateral for an upper and a
    lower one. Enter in any field accepts, so the whole insertion stays
    a keyboard gesture.
    """

    accepted = Signal(object)  # Tolerance

    def __init__(
        self, mode: ToleranceMode, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._mode = mode
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)
        if mode is ToleranceMode.SYMMETRIC:
            fields = ((PLUS_MINUS, "0.05"),)
        else:
            fields = (("+", "0.10"), ("-", "0.05"))
        self._edits: list[QLineEdit] = []
        for sign, placeholder in fields:
            lay.addWidget(QLabel(sign, self))
            edit = QLineEdit(self)
            edit.setPlaceholderText(placeholder)
            edit.setFixedWidth(58)
            edit.returnPressed.connect(self._accept)
            lay.addWidget(edit)
            self._edits.append(edit)
        ok = QToolButton(self)
        ok.setText("OK")
        ok.setToolTip("Insert this tolerance (Enter)")
        ok.setFocusPolicy(Qt.NoFocus)
        ok.clicked.connect(self._accept)
        lay.addWidget(ok)

    def tolerance(self) -> Tolerance:
        if self._mode is ToleranceMode.SYMMETRIC:
            return Tolerance(
                mode=self._mode, value=self._edits[0].text().strip()
            )
        return Tolerance(
            mode=self._mode,
            upper=self._edits[0].text().strip(),
            lower=self._edits[1].text().strip(),
        )

    def reset(self) -> None:
        for edit in self._edits:
            edit.clear()

    def focus_first(self) -> None:
        self._edits[0].setFocus()
        self._edits[0].selectAll()

    def _accept(self) -> None:
        tol = self.tolerance()
        if tol.is_empty():
            return
        self.accepted.emit(tol)


class EditToolbar(QFrame):
    """Floating pill of edit-session actions. Parent it to the view's
    viewport; MainWindow drives visibility and position."""

    fontSizeChanged = Signal(int)  # absolute new point size
    boldToggled = Signal(bool)
    italicToggled = Signal(bool)
    alignPicked = Signal(object)  # TextAlign
    borderPicked = Signal(object)  # TextBorder
    symbolPicked = Signal(str)  # the character(s) to insert at the caret
    tolerancePicked = Signal(object)  # Tolerance
    # Emitted just after one of the pill's menus closed: the popup stole
    # keyboard focus from the graphics view and the caller has to hand it
    # back to the item being edited.
    refocusRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("EditToolbar")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_TOOLBAR_QSS)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(4, 3, 4, 3)
        self._lay.setSpacing(2)
        # Same proven fix as the selection pill and the GD&T editor: the
        # widget tracks its layout's size, so a rebuilt bar can never
        # linger as a tiny empty square.
        self._lay.setSizeConstraint(QHBoxLayout.SizeConstraint.SetFixedSize)
        self._icon_color = QColor("#212121")
        self._item: TextAnnotationItem | None = None
        self._font_size: int = 0
        self._size_label: QLabel | None = None
        self._tolerance_menu: QMenu | None = None
        self._tolerance_forms: dict[ToleranceMode, _ToleranceForm] = {}
        self.hide()

    # ------------------------------------------------------------------
    # context
    # ------------------------------------------------------------------
    def item(self) -> TextAnnotationItem | None:
        """The item the bar was last built for (None when hidden)."""
        return self._item

    def set_context(
        self, item: object, icon_color: QColor | None = None
    ) -> None:
        """Rebuild the bar for `item` (None or an unsupported kind hides)."""
        if icon_color is not None:
            self._icon_color = QColor(icon_color)
        self._clear()
        self._item = None
        if isinstance(item, TextAnnotationItem):
            self._item = item
            self._build_text(item)
        # other item kinds plug in here, one `_build_*` per kind --
        # same branch structure as SelectionToolbar._build_single:
        #     elif isinstance(item, DimensionAnnotationItem):
        #         self._build_dimension(item)
        else:
            self.hide()
            return
        # Force a synchronous relayout before measuring: adjustSize() on
        # a hidden, just-rebuilt widget can act on a stale sizeHint.
        self._lay.invalidate()
        self._lay.activate()
        self.resize(self.sizeHint())

    def _clear(self) -> None:
        self._size_label = None
        self._tolerance_menu = None
        self._tolerance_forms = {}
        while self._lay.count():
            child = self._lay.takeAt(0)
            w = child.widget()
            if w is not None:
                # Detach immediately: deleteLater alone keeps the old
                # buttons visible (and findable) until the event loop
                # runs, so a rebuilt bar would briefly show both sets.
                w.setParent(None)
                w.deleteLater()

    # ------------------------------------------------------------------
    # small builders
    # ------------------------------------------------------------------
    def _button(
        self, text: str, tooltip: str, checkable: bool = False
    ) -> QToolButton:
        btn = QToolButton(self)
        btn.setText(text)
        btn.setToolTip(tooltip)
        btn.setCheckable(checkable)
        # Focus is the whole game here: a focusable button would pull the
        # keyboard away from the graphics view and end the edit session.
        btn.setFocusPolicy(Qt.NoFocus)
        self._lay.addWidget(btn)
        return btn

    def _menu_button(self, text: str, tooltip: str) -> tuple[QToolButton, QMenu]:
        btn = self._button(text, tooltip)
        btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn)
        menu.aboutToHide.connect(self._schedule_refocus)
        btn.setMenu(menu)
        return btn, menu

    def _v_separator(self) -> None:
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        self._lay.addWidget(line)

    def _schedule_refocus(self) -> None:
        # Deferred by one event-loop turn, like GdtInlineEditor's
        # `_refocus_after_menu`: the popup is still closing when
        # aboutToHide fires, so taking focus back now would be undone.
        QTimer.singleShot(0, self.refocusRequested.emit)

    # ------------------------------------------------------------------
    # text variant
    # ------------------------------------------------------------------
    def _build_text(self, item: TextAnnotationItem) -> None:
        self._build_font_size(item)
        self._v_separator()
        bold = self._button("B", "Bold", checkable=True)
        bold.setChecked(bool(item.bold()))
        bold.toggled.connect(self.boldToggled)
        italic = self._button("I", "Italic", checkable=True)
        italic.setChecked(bool(item.italic()))
        italic.toggled.connect(self.italicToggled)
        self._build_align(item)
        self._build_border(item)
        self._v_separator()
        self._build_symbols()
        self._build_tolerance()

    def _build_font_size(self, item: TextAnnotationItem) -> None:
        self._font_size = int(item.font_size())
        minus = self._button("-", "Smaller text")
        minus.clicked.connect(lambda: self._step_font_size(-1))
        label = QLabel(str(self._font_size), self)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumWidth(24)
        label.setToolTip("Font size (points)")
        self._lay.addWidget(label)
        self._size_label = label
        plus = self._button("+", "Larger text")
        plus.clicked.connect(lambda: self._step_font_size(1))

    def _step_font_size(self, direction: int) -> None:
        size = (
            _next_ladder_up(self._font_size)
            if direction > 0
            else _next_ladder_down(self._font_size)
        )
        if size == self._font_size:
            return
        self._font_size = size
        if self._size_label is not None:
            self._size_label.setText(str(size))
        self.fontSizeChanged.emit(size)

    def _build_align(self, item: TextAnnotationItem) -> None:
        _btn, menu = self._menu_button("Align", "Text alignment")
        current = item.align()
        for align, label in _ALIGN_LABELS:
            act = menu.addAction(
                align_icon(align, color=self._icon_color), label
            )
            act.setCheckable(True)
            act.setChecked(align is current)
            act.triggered.connect(
                lambda _c=False, a=align: self.alignPicked.emit(a)
            )

    def _build_border(self, item: TextAnnotationItem) -> None:
        _btn, menu = self._menu_button("Border", "Outline around the text")
        current = item.border()
        for border, label in TEXT_BORDER_LABELS:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(border is current)
            act.triggered.connect(
                lambda _c=False, b=border: self.borderPicked.emit(b)
            )

    def _build_symbols(self) -> None:
        _btn, menu = self._menu_button(
            "Symbol", "Insert a drawing symbol at the caret"
        )
        header = menu.addAction("PDF text (WinAnsi)")
        header.setEnabled(False)
        for char, name in _WINANSI_SYMBOLS:
            act = menu.addAction(f"{char}   {name}")
            act.triggered.connect(
                lambda _c=False, s=char: self.symbolPicked.emit(s)
            )
        menu.addSeparator()
        header = menu.addAction("Needs an appearance stream")
        header.setEnabled(False)
        for char, name in _WIDE_SYMBOLS:
            act = menu.addAction(f"{char}   {name}")
            act.triggered.connect(
                lambda _c=False, s=char: self.symbolPicked.emit(s)
            )

    def _build_tolerance(self) -> None:
        _btn, menu = self._menu_button(
            "Tolerance", "Insert a tolerance run at the caret"
        )
        self._tolerance_menu = menu
        for mode in (ToleranceMode.SYMMETRIC, ToleranceMode.BILATERAL):
            sub = menu.addMenu(MODE_NAMES[mode])
            form = _ToleranceForm(mode, sub)
            form.accepted.connect(self._on_tolerance_accepted)
            action = QWidgetAction(sub)
            action.setDefaultWidget(form)
            sub.addAction(action)
            sub.aboutToShow.connect(form.focus_first)
            self._tolerance_forms[mode] = form

    def _on_tolerance_accepted(self, tol: Tolerance) -> None:
        for form in self._tolerance_forms.values():
            form.reset()
        if self._tolerance_menu is not None:
            # Closing the root closes the open submenu with it, and its
            # aboutToHide is what schedules the refocus.
            self._tolerance_menu.hide()
        self.tolerancePicked.emit(tol)
