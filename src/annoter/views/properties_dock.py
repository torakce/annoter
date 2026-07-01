"""PropertiesDock: per-type property editor for the current selection.

The dock listens to the scene's selectionChanged signal and rebuilds an
appropriate form. Numeric/text fields (QSpinBox, QDoubleSpinBox,
QLineEdit) use a live-preview-then-commit pattern: every keystroke or
spin-arrow click applies the change directly to the item(s) so the
canvas updates immediately (no need to click elsewhere first), while
the undo command is only pushed once, when the field loses focus or
Enter is pressed -- so a whole editing session (e.g. clicking the
stroke spinner five times) still lands as a single undo step, exactly
like a mouse drag. See `_wire_live_prop` / `_wire_live_geom`.
Checkboxes, combo boxes and the color-picker button are already
"live" by nature (their signals only fire on a real, discrete choice),
so they still commit straight away via `_push_prop`.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QUndoStack
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from annoter.controllers.commands import (
    ChangePropsCommand,
    MoveAnnotationsCommand,
    ResizeCommand,
)
from annoter.controllers.geometry import (
    item_local_rect,
    item_scene_rect,
    move_delta_for_rect,
    pt_to_px,
    px_to_pt,
)
from annoter.model.styles import DashStyle, EndStyle, TextAlign
from annoter.views.icons import align_icon, dash_icon, end_icon
from annoter.views.items.base import AnnotationItem
from annoter.views.items.freehand import FreehandItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, EllipseItem, RectangleItem
from annoter.views.items.stamp import STAMP_PRESETS, StampItem
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
        if len(self._items) == 1:
            self._add_geometry_rows(form, self._items[0])
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
            elif issubclass(cls, StampItem):
                self._add_stamp_rows(form)
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
        self._wire_live_prop(spin, "stroke", transform=float)
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

    def _add_geometry_rows(self, form: QFormLayout, item: AnnotationItem) -> None:
        """Precise numeric position/size, mirroring the live measurement
        HUD shown while dragging (same unit: PDF points). Live-previews
        on every change (see `_wire_live_geom`) and lands as the same
        undo command a manual drag would produce, once."""
        rect = item_scene_rect(item)

        x_spin = self._geometry_spin(px_to_pt(rect.x()))
        self._wire_live_geom(
            x_spin,
            lambda v: self._apply_move_live(item, x=v),
            lambda: QPointF(item.pos()),
            lambda orig: self._commit_move(item, orig),
        )
        form.addRow("X (pt)", x_spin)

        y_spin = self._geometry_spin(px_to_pt(rect.y()))
        self._wire_live_geom(
            y_spin,
            lambda v: self._apply_move_live(item, y=v),
            lambda: QPointF(item.pos()),
            lambda orig: self._commit_move(item, orig),
        )
        form.addRow("Y (pt)", y_spin)

        if isinstance(item, LineItem):
            p1, p2 = item.line_points()
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            length_px = math.hypot(dx, dy)
            angle_deg = (-math.degrees(math.atan2(dy, dx))) % 360.0

            len_spin = self._geometry_spin(
                px_to_pt(length_px), minimum=0.1, maximum=100000.0
            )
            self._wire_live_geom(
                len_spin,
                lambda v: self._apply_line_geom_live(item, length_pt=v),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            form.addRow("Length (pt)", len_spin)

            ang_spin = self._geometry_spin(
                angle_deg, minimum=-3600.0, maximum=3600.0
            )
            self._wire_live_geom(
                ang_spin,
                lambda v: self._apply_line_geom_live(item, angle_deg=v),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            form.addRow("Angle (deg)", ang_spin)
        elif hasattr(item, "rect") and hasattr(item, "set_rect"):
            w_spin = self._geometry_spin(
                px_to_pt(rect.width()), minimum=0.1, maximum=100000.0
            )
            self._wire_live_geom(
                w_spin,
                lambda v: self._apply_resize_live(item, w=v),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            form.addRow("Width (pt)", w_spin)

            h_spin = self._geometry_spin(
                px_to_pt(rect.height()), minimum=0.1, maximum=100000.0
            )
            self._wire_live_geom(
                h_spin,
                lambda v: self._apply_resize_live(item, h=v),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            form.addRow("Height (pt)", h_spin)

    @staticmethod
    def _geometry_spin(
        value: float, *, minimum: float = -100000.0, maximum: float = 100000.0
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(1)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    # -- live preview halves (no undo) --------------------------------
    def _apply_move_live(
        self, item: AnnotationItem, *, x: float | None = None, y: float | None = None
    ) -> None:
        current = item_scene_rect(item)
        target_x_px = pt_to_px(x) if x is not None else current.x()
        target_y_px = pt_to_px(y) if y is not None else current.y()
        delta = move_delta_for_rect(item, QPointF(target_x_px, target_y_px))
        item.setPos(item.pos() + delta)

    def _apply_resize_live(
        self, item: AnnotationItem, *, w: float | None = None, h: float | None = None
    ) -> None:
        local = item_local_rect(item)
        new_w = pt_to_px(w) if w is not None else local.width()
        new_h = pt_to_px(h) if h is not None else local.height()
        if new_w <= 0 or new_h <= 0:
            return
        item.set_rect(QRectF(local.x(), local.y(), new_w, new_h))

    def _apply_line_geom_live(
        self,
        item: AnnotationItem,
        *,
        length_pt: float | None = None,
        angle_deg: float | None = None,
    ) -> None:
        p1, p2 = item.line_points()
        dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
        cur_length = math.hypot(dx, dy)
        cur_angle = (-math.degrees(math.atan2(dy, dx))) % 360.0
        target_length = (
            pt_to_px(length_pt) if length_pt is not None else cur_length
        )
        target_angle = angle_deg if angle_deg is not None else cur_angle
        if target_length <= 0:
            return
        math_angle = math.radians((-target_angle) % 360.0)
        new_p2 = QPointF(
            p1.x() + target_length * math.cos(math_angle),
            p1.y() + target_length * math.sin(math_angle),
        )
        item.set_line_points(p1, new_p2)

    # -- commit halves (pushes the undo command once) -----------------
    def _commit_move(self, item: AnnotationItem, original_pos: QPointF) -> None:
        new_pos = QPointF(item.pos())
        if (new_pos - original_pos).manhattanLength() < 1e-6:
            return
        self._push_command(
            MoveAnnotationsCommand(
                [(item, original_pos, new_pos)], label="Move annotation"
            )
        )

    def _commit_resize(self, item: AnnotationItem, original_snapshot: object) -> None:
        new = item.geom_snapshot()
        if original_snapshot == new:
            return
        self._push_command(ResizeCommand(item, original_snapshot, new))

    # -- single-shot convenience wrappers (apply + commit in one call,
    # e.g. for callers that don't need live preview) ------------------
    def _push_move(
        self, item: AnnotationItem, *, x: float | None = None, y: float | None = None
    ) -> None:
        original_pos = QPointF(item.pos())
        self._apply_move_live(item, x=x, y=y)
        self._commit_move(item, original_pos)

    def _push_resize_rect(
        self, item: AnnotationItem, *, w: float | None = None, h: float | None = None
    ) -> None:
        original = item.geom_snapshot()
        self._apply_resize_live(item, w=w, h=h)
        self._commit_resize(item, original)

    def _push_line_geom(
        self,
        item: AnnotationItem,
        *,
        length_pt: float | None = None,
        angle_deg: float | None = None,
    ) -> None:
        original = item.geom_snapshot()
        self._apply_line_geom_live(item, length_pt=length_pt, angle_deg=angle_deg)
        self._commit_resize(item, original)

    def _push_command(self, cmd) -> None:
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()

    # ------------------------------------------------------------------
    # live-preview-then-commit wiring (see module docstring)
    # ------------------------------------------------------------------
    def _live_prop(self, name: str, value: object) -> None:
        """Apply `name` directly to every selected item, no undo -- the
        instant-feedback half of `_wire_live_prop`."""
        for it in self._items:
            setter = getattr(it, f"set_{name}", None)
            if setter is not None:
                setter(value)

    def _wire_live_prop(
        self,
        widget,
        name: str,
        *,
        is_line_edit: bool = False,
        transform: Callable[[object], object] = lambda v: v,
    ) -> None:
        """Wire a QSpinBox/QDoubleSpinBox/QLineEdit so every change
        previews live and a single `ChangePropsCommand` commits once
        editing finishes. The pre-edit values are captured lazily, on
        the *first* change of each editing session -- not when the
        dock/field was built -- so editing the same field again later,
        or editing a sibling field first, never uses a stale baseline.
        """
        originals: dict[AnnotationItem, object] = {}

        def current_value():
            raw = widget.text() if is_line_edit else widget.value()
            return transform(raw)

        def on_change(*_args) -> None:
            if not originals:
                for it in self._items:
                    try:
                        originals[it] = getattr(it, name)()
                    except AttributeError:
                        continue
            self._live_prop(name, current_value())

        def on_finish() -> None:
            if not originals:
                return
            value = current_value()
            changes = [
                (it, name, old, value)
                for it, old in originals.items()
                if old != value
            ]
            originals.clear()
            if changes:
                self._push_command(ChangePropsCommand(changes))

        if is_line_edit:
            widget.textChanged.connect(on_change)
        else:
            widget.valueChanged.connect(on_change)
        widget.editingFinished.connect(on_finish)

    def _wire_live_geom(
        self,
        spin: QDoubleSpinBox,
        apply_live: Callable[[float], None],
        get_snapshot: Callable[[], object],
        commit: Callable[[object], None],
    ) -> None:
        """Like `_wire_live_prop`, but for the single-item geometry
        fields, which use `MoveAnnotationsCommand` / `ResizeCommand`
        (opaque snapshots) instead of `ChangePropsCommand`."""
        state: dict[str, object] = {"original": None}

        def on_change(value: float) -> None:
            if state["original"] is None:
                state["original"] = get_snapshot()
            apply_live(value)

        def on_finish() -> None:
            if state["original"] is None:
                return
            commit(state["original"])
            state["original"] = None

        spin.valueChanged.connect(on_change)
        spin.editingFinished.connect(on_finish)

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
        self._add_fill_opacity_row(form, first)

        if with_corner:
            spin = QSpinBox()
            spin.setRange(0, 200)
            spin.setValue(int(round(first.corner_radius())))
            self._wire_live_prop(spin, "corner_radius", transform=float)
            form.addRow("Corner radius", spin)

        # Text label
        text_edit = QLineEdit()
        text_edit.setText(first.text())
        self._wire_live_prop(text_edit, "text", is_line_edit=True)
        form.addRow("Text", text_edit)

        # Label font size
        lbl_size = QSpinBox()
        lbl_size.setRange(4, 96)
        lbl_size.setValue(int(first.label_font_size()))
        self._wire_live_prop(lbl_size, "label_font_size", transform=int)
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
        self._add_fill_opacity_row(form, first)

    def _add_fill_opacity_row(self, form: QFormLayout, first) -> None:
        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix(" %")
        spin.setValue(int(round(first.fill_opacity() * 100)))
        self._wire_live_prop(
            spin, "fill_opacity", transform=lambda v: v / 100.0
        )
        form.addRow("Fill opacity", spin)

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
        self._wire_live_prop(size, "font_size", transform=int)
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

    def _add_stamp_rows(self, form: QFormLayout) -> None:
        first = self._items[0]

        preset = QComboBox()
        for label, _hex in STAMP_PRESETS:
            preset.addItem(label, label)
        preset.addItem("Custom...", None)
        match = next(
            (i for i, (lbl, _h) in enumerate(STAMP_PRESETS)
             if lbl == first.text()),
            len(STAMP_PRESETS),  # "Custom"
        )
        preset.setCurrentIndex(match)
        preset.activated.connect(
            lambda _i, c=preset: self._apply_stamp_preset(c.currentData())
        )
        form.addRow("Preset", preset)

        text_edit = QLineEdit()
        text_edit.setText(first.text())
        self._wire_live_prop(text_edit, "text", is_line_edit=True)
        form.addRow("Text", text_edit)

        size = QSpinBox()
        size.setRange(6, 96)
        size.setValue(int(first.font_size()))
        self._wire_live_prop(size, "font_size", transform=int)
        form.addRow("Size", size)

    def _apply_stamp_preset(self, label: object) -> None:
        """Set a preset's text and color in one undo step; 'Custom' is a
        no-op (the user edits the text/color fields directly)."""
        if not label or not self._items:
            return
        color_hex = next(
            (h for lbl, h in STAMP_PRESETS if lbl == label), None
        )
        if color_hex is None:
            return
        new_color = QColor(color_hex)
        changes = []
        for it in self._items:
            if it.text() != label:
                changes.append((it, "text", it.text(), str(label)))
            if it.color() != new_color:
                changes.append((it, "color", it.color(), QColor(new_color)))
        if not changes:
            return
        cmd = ChangePropsCommand(changes)
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()
        self._rebuild()

    def _add_gdt_rows(self, form: QFormLayout) -> None:
        first = self._items[0]
        size = QSpinBox()
        size.setRange(6, 72)
        size.setValue(int(first.font_size()))
        self._wire_live_prop(size, "font_size", transform=int)
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
