"""PropertiesDock: per-type property editor for the current selection.

The dock listens to the scene's selectionChanged signal, rebuilds an
appropriate form, and pushes a `ChangePropsCommand` whenever an editor
emits its "editing finished" signal. Live previews use the same path
so each user gesture lands as one undo step.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QUndoStack
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.controllers.commands import ChangePropsCommand
from annoter.model.styles import DashStyle, EndStyle, TextAlign
from annoter.views.icons import align_icon, dash_icon, end_icon
from annoter.views.items.base import AnnotationItem
from annoter.views.items.freehand import FreehandItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, EllipseItem, RectangleItem
from annoter.views.items.text import TEXT_FONT_FAMILIES, TextAnnotationItem


_DASH_LABELS: list[tuple[DashStyle, str]] = [
    (DashStyle.SOLID, "Solid"),
    (DashStyle.DASHED, "Dashed"),
    (DashStyle.DOTTED, "Dotted"),
    (DashStyle.DASH_DOT, "Dash-dot"),
    (DashStyle.DASH_DOT_DOT, "Dash-dot-dot"),
]

_END_LABELS: list[tuple[EndStyle, str]] = [
    (EndStyle.NONE, "None"),
    (EndStyle.OPEN_ARROW, "Open arrow"),
    (EndStyle.CLOSED_ARROW, "Closed arrow"),
    (EndStyle.BUTT, "Butt (perp. tick)"),
    (EndStyle.SLASH, "Slash"),
    (EndStyle.DIAMOND, "Diamond"),
    (EndStyle.CIRCLE, "Circle"),
    (EndStyle.SQUARE, "Square"),
]

_ALIGN_LABELS: list[tuple[TextAlign, str]] = [
    (TextAlign.LEFT, "Left"),
    (TextAlign.CENTER, "Center"),
    (TextAlign.RIGHT, "Right"),
]


def _color_button(color: QColor) -> QPushButton:
    btn = QPushButton()
    btn.setFixedHeight(22)
    btn.setStyleSheet(
        f"background: {color.name()}; border: 1px solid #888;"
    )
    return btn


class PropertiesDock(QDockWidget):
    """Right-side dock; rebuilds its body when the selection changes."""

    propsChangeRequested = Signal(object)  # ChangePropsCommand

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Properties", parent)
        self.setObjectName("PropertiesDock")
        self._undo_stack: QUndoStack | None = None
        self._items: list[AnnotationItem] = []

        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(8, 8, 8, 8)
        self._body_layout.setSpacing(8)
        self._empty_label = QLabel("No annotation selected.")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._body_layout.addWidget(self._empty_label)
        self._body_layout.addStretch(1)
        self.setWidget(self._body)

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    def set_undo_stack(self, stack: QUndoStack | None) -> None:
        self._undo_stack = stack

    def set_items(self, items: list[AnnotationItem]) -> None:
        self._items = list(items)
        self._rebuild()

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------
    def _clear_body(self) -> None:
        while self._body_layout.count():
            child = self._body_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear_body()
        if not self._items:
            lbl = QLabel("No annotation selected.")
            lbl.setAlignment(Qt.AlignCenter)
            self._body_layout.addWidget(lbl)
            self._body_layout.addStretch(1)
            return

        # When the selection mixes types we show only the common props
        # (color, stroke, dash). Otherwise we render the type-specific
        # form on top of that.
        types = {type(it) for it in self._items}
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self._add_common_rows(form)
        if len(types) == 1:
            cls = next(iter(types))
            if issubclass(cls, RectangleItem):
                self._add_shape_rows(form, with_corner=True)
            elif issubclass(cls, EllipseItem):
                self._add_shape_rows(form, with_corner=False)
            elif issubclass(cls, CloudItem):
                self._add_fill_rows(form)
            elif issubclass(cls, PolygonItem):
                self._add_fill_rows(form)
            elif issubclass(cls, PolylineItem):
                pass  # base + dash already covered
            elif issubclass(cls, ArrowItem):
                self._add_arrow_rows(form)
            elif issubclass(cls, LineItem):
                pass  # base + dash already covered
            elif issubclass(cls, TextAnnotationItem):
                self._add_text_rows(form)
            elif issubclass(cls, GdtAnnotationItem):
                self._add_gdt_rows(form)
            elif issubclass(cls, FreehandItem):
                pass

        host = QWidget()
        host.setLayout(form)
        self._body_layout.addWidget(host)
        self._body_layout.addStretch(1)

    # ------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------
    def _add_common_rows(self, form: QFormLayout) -> None:
        # Stroke color
        first = self._items[0]
        color_btn = _color_button(first.color())
        color_btn.clicked.connect(
            lambda: self._pick_color("color", first.color(), color_btn)
        )
        form.addRow("Color", color_btn)

        # Stroke width
        spin = QSpinBox()
        spin.setRange(1, 20)
        spin.setValue(int(round(first.stroke())))
        spin.editingFinished.connect(
            lambda s=spin: self._push_prop("stroke", float(s.value()))
        )
        form.addRow("Stroke", spin)

        # Dash style
        combo = self._enum_combo(
            _DASH_LABELS, first.dash_style(), icon_for=dash_icon
        )
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: self._push_prop(
                "dash_style", c.currentData()
            )
        )
        form.addRow("Dash", combo)

    def _add_shape_rows(
        self, form: QFormLayout, with_corner: bool
    ) -> None:
        first = self._items[0]
        # Fill enabled
        cb = QCheckBox("Filled")
        cb.setChecked(bool(first.fill_enabled()))
        cb.toggled.connect(
            lambda checked: self._push_prop("fill_enabled", bool(checked))
        )
        form.addRow("Fill", cb)

        # Fill color
        fc_btn = _color_button(first.fill_color())
        fc_btn.clicked.connect(
            lambda: self._pick_color(
                "fill_color", first.fill_color(), fc_btn
            )
        )
        form.addRow("Fill color", fc_btn)

        if with_corner:
            spin = QSpinBox()
            spin.setRange(0, 200)
            spin.setValue(int(round(first.corner_radius())))
            spin.editingFinished.connect(
                lambda s=spin: self._push_prop(
                    "corner_radius", float(s.value())
                )
            )
            form.addRow("Corner radius", spin)

        # Text label
        text_edit = QLineEdit()
        text_edit.setText(first.text())
        text_edit.editingFinished.connect(
            lambda e=text_edit: self._push_prop("text", e.text())
        )
        form.addRow("Text", text_edit)

        # Label font size
        lbl_size = QSpinBox()
        lbl_size.setRange(4, 96)
        lbl_size.setValue(int(first.label_font_size()))
        lbl_size.editingFinished.connect(
            lambda s=lbl_size: self._push_prop(
                "label_font_size", int(s.value())
            )
        )
        form.addRow("Text size", lbl_size)

    def _add_fill_rows(self, form: QFormLayout) -> None:
        first = self._items[0]
        cb = QCheckBox("Filled")
        cb.setChecked(bool(first.fill_enabled()))
        cb.toggled.connect(
            lambda checked: self._push_prop("fill_enabled", bool(checked))
        )
        form.addRow("Fill", cb)

        fc_btn = _color_button(first.fill_color())
        fc_btn.clicked.connect(
            lambda: self._pick_color(
                "fill_color", first.fill_color(), fc_btn
            )
        )
        form.addRow("Fill color", fc_btn)

    def _add_arrow_rows(self, form: QFormLayout) -> None:
        first = self._items[0]
        c1 = self._enum_combo(
            _END_LABELS, first.start_end(), icon_for=end_icon
        )
        c1.currentIndexChanged.connect(
            lambda _i, c=c1: self._push_prop("start_end", c.currentData())
        )
        form.addRow("Start", c1)
        c2 = self._enum_combo(
            _END_LABELS, first.end_end(), icon_for=end_icon
        )
        c2.currentIndexChanged.connect(
            lambda _i, c=c2: self._push_prop("end_end", c.currentData())
        )
        form.addRow("End", c2)

    def _add_text_rows(self, form: QFormLayout) -> None:
        first = self._items[0]
        family = QComboBox()
        for f in TEXT_FONT_FAMILIES:
            family.addItem(f, f)
        idx = family.findData(first.font_family())
        family.setCurrentIndex(max(0, idx))
        family.currentIndexChanged.connect(
            lambda _i, c=family: self._push_prop(
                "font_family", c.currentData()
            )
        )
        form.addRow("Font", family)

        size = QSpinBox()
        size.setRange(4, 144)
        size.setValue(int(first.font_size()))
        size.editingFinished.connect(
            lambda s=size: self._push_prop("font_size", int(s.value()))
        )
        form.addRow("Size", size)

        bold = QCheckBox("Bold")
        bold.setChecked(bool(first.bold()))
        bold.toggled.connect(
            lambda checked: self._push_prop("bold", bool(checked))
        )

        italic = QCheckBox("Italic")
        italic.setChecked(bool(first.italic()))
        italic.toggled.connect(
            lambda checked: self._push_prop("italic", bool(checked))
        )

        row = QHBoxLayout()
        row.addWidget(bold)
        row.addWidget(italic)
        row.addStretch(1)
        host = QWidget()
        host.setLayout(row)
        form.addRow("Style", host)

        align = self._enum_combo(
            _ALIGN_LABELS, first.align(), icon_for=align_icon
        )
        align.currentIndexChanged.connect(
            lambda _i, c=align: self._push_prop("align", c.currentData())
        )
        form.addRow("Align", align)

    def _add_gdt_rows(self, form: QFormLayout) -> None:
        first = self._items[0]
        size = QSpinBox()
        size.setRange(6, 72)
        size.setValue(int(first.font_size()))
        size.editingFinished.connect(
            lambda s=size: self._push_prop("font_size", int(s.value()))
        )
        form.addRow("Frame size", size)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _enum_combo(
        self,
        entries: list[tuple[object, str]],
        current: object,
        icon_for=None,
    ) -> QComboBox:
        combo = QComboBox()
        if icon_for is not None:
            combo.setIconSize(QSize(48, 16))
        for value, label in entries:
            if icon_for is not None:
                combo.addItem(icon_for(value), label, value)
            else:
                combo.addItem(label, value)
        idx = next(
            (i for i, (v, _l) in enumerate(entries) if v is current),
            0,
        )
        combo.setCurrentIndex(idx)
        return combo

    def _pick_color(
        self, prop: str, initial: QColor, button: QPushButton
    ) -> None:
        c = QColorDialog.getColor(initial, self, "Pick color")
        if not c.isValid():
            return
        self._push_prop(prop, QColor(c))
        button.setStyleSheet(
            f"background: {c.name()}; border: 1px solid #888;"
        )

    def _push_prop(self, name: str, new_value: object) -> None:
        if not self._items:
            return
        getter: Callable[[AnnotationItem], object]
        getter = lambda it, n=name: getattr(it, n)()  # noqa: E731
        changes = []
        for it in self._items:
            try:
                old = getter(it)
            except AttributeError:
                continue
            if old == new_value:
                continue
            changes.append((it, name, old, new_value))
        if not changes:
            return
        cmd = ChangePropsCommand(changes)
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()
