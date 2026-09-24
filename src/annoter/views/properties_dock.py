"""PropertiesDock: the inspector -- settings of what is selected.

UI redesign, Lot F. The dock used to be one long label / field form.
It is now a context-aware inspector:

- a header naming what is selected (type icon in its color, "Revision
  cloud", "3 annotations") or, with nothing selected, "Nothing selected";
- sections (Style, Shape, Label, Ends, Text, Stamp, GD&T frame,
  Position & size, Arrange) holding friendlier controls: palette
  swatches instead of a color button, stroke presets plus an exact
  field, segmented controls instead of short combo boxes, a mm / pt
  switch for positions;
- Duplicate / Delete at the bottom;
- with nothing selected, the color and stroke of the NEXT annotation
  (this replaces the old toolbar quick styles) and a few keyboard tips.

Unchanged underneath: numeric / text fields use a live-preview-then-
commit pattern -- every keystroke or spin-arrow click applies the change
directly to the item(s) so the canvas updates immediately, while the
undo command is pushed once, when the field loses focus or Enter is
pressed (see `_wire_live_prop` / `_wire_live_geom`). Discrete controls
(swatches, segments, check boxes, combo boxes) commit straight away.

Color and stroke presets are emitted (`colorPicked`, `strokePicked`)
rather than applied here: MainWindow applies them to the selection AND
makes them the drawing default, as the toolbar quick styles did
(Discussion #1 follow-up). `field(label)` returns a row's input widget.
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QUndoStack
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.controllers.commands import (
    ChangePropsCommand,
    MoveAnnotationsCommand,
    ReplaceAnnotationCommand,
    ResizeCommand,
)
from annoter.controllers.convert import (
    callout_to_arrow,
    convert_poly_closed,
    convert_shape_outline,
)
from annoter.controllers.geometry import (
    item_local_rect,
    item_scene_rect,
    move_delta_for_rect,
    pt_to_px,
    px_to_pt,
)
from annoter.model.styles import (
    END_STYLE_LABELS,
    TEXT_BORDER_LABELS,
    DashStyle,
    TextAlign,
)
from annoter.services.palette import PaletteStore
from annoter.views.annotation_list import describe
from annoter.views.color_picker import popup_color_picker
from annoter.views.icons import align_icon, dash_icon, end_icon
from annoter.views.inspector_widgets import (
    ColorSwatchRow,
    SegmentedControl,
    StrokeField,
)
from annoter.views.items.base import AnnotationItem
from annoter.views.items.callout import CalloutItem
from annoter.views.items.freehand import FreehandItem
from annoter.views.items.gdt import GdtAnnotationItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import CloudItem, EllipseItem, RectangleItem
from annoter.views.items.stamp import STAMP_PRESETS, StampItem
from annoter.views.items.text import TEXT_FONT_FAMILIES, TextAnnotationItem
from annoter.views.line_icons import line_icon, line_pixmap


_DASH_LABELS: list[tuple[DashStyle, str]] = [
    (DashStyle.SOLID, "Solid"),
    (DashStyle.DASHED, "Dashed"),
    (DashStyle.DOTTED, "Dotted"),
    (DashStyle.DASH_DOT, "Dash-dot"),
    (DashStyle.DASH_DOT_DOT, "Dash-dot-dot"),
]

_END_LABELS = END_STYLE_LABELS  # shared with the endpoint context menu

_ALIGN_LABELS: list[tuple[TextAlign, str]] = [
    (TextAlign.LEFT, "Left"),
    (TextAlign.CENTER, "Center"),
    (TextAlign.RIGHT, "Right"),
]

INSPECTOR_MIN_WIDTH = 300
INSPECTOR_WIDTH = 320

# Position units: the PDF works in points, mechanical drawings in mm.
UNITS = ("mm", "pt")
_MM_PER_PT = 25.4 / 72.0

_TIPS: list[tuple[str, str]] = [
    ("Space", "+ drag to move around"),
    ("Ctrl", "+ wheel to zoom at the cursor"),
    ("Shift", "keeps angles and proportions"),
    ("Esc", "back to Select at any time"),
    ("Ctrl K", "find any command or tool"),
]


def _color_button(color: QColor) -> QPushButton:
    btn = QPushButton()
    btn.setObjectName("InspectorColorButton")
    btn.setFixedSize(44, 24)
    btn.setToolTip(color.name().upper())
    btn.setStyleSheet(
        f"QPushButton#InspectorColorButton {{ background: {color.name()};"
        " border: 1px solid rgba(0,0,0,0.25); border-radius: 6px; }"
    )
    return btn


class _Section(QFrame):
    """Titled block of rows."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("InspectorSection")
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        self.title = QLabel(title.upper(), self)
        self.title.setObjectName("InspectorSectionTitle")
        self.header.addWidget(self.title, 1)
        col.addLayout(self.header)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.form.setHorizontalSpacing(10)
        self.form.setVerticalSpacing(8)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        col.addLayout(self.form)


class PropertiesDock(QDockWidget):
    """Right-side inspector; rebuilt when the selection changes."""

    colorPicked = Signal(QColor)  # swatch / picker (selection or default)
    strokePicked = Signal(float)  # preset or default width
    strokeCommitted = Signal(float)  # exact field committed on a selection
    editPaletteRequested = Signal()
    editRequested = Signal()  # GD&T / dimension / note editor
    duplicateRequested = Signal()
    deleteRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        palette: PaletteStore | None = None,
    ) -> None:
        super().__init__("Properties", parent)
        self.setObjectName("PropertiesDock")
        self.setFeatures(QDockWidget.DockWidgetClosable)
        self.setTitleBarWidget(QWidget(self))  # the header is in the body
        self.setMinimumWidth(INSPECTOR_MIN_WIDTH)
        self._undo_stack: QUndoStack | None = None
        self._items: list[AnnotationItem] = []
        self._icon_color: QColor = QColor("#212121")
        self._ring_color: QColor = QColor("#212121")
        self._palette = palette if palette is not None else PaletteStore()
        self._fields: dict[str, QWidget] = {}
        self._unit = "mm"
        self._default_color = QColor("#E53935")
        self._default_stroke = 2.0
        self._actions: dict[str, QAction] = {}

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("InspectorScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        # Never clip a control: scroll sideways if the dock is squeezed.
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._body = QWidget(self._scroll)
        self._body.setObjectName("InspectorBody")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._scroll.setWidget(self._body)
        self.setWidget(self._scroll)
        self._rebuild()

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    def set_undo_stack(self, stack: QUndoStack | None) -> None:
        self._undo_stack = stack

    def set_items(self, items: list[AnnotationItem]) -> None:
        self._items = list(items)
        self._rebuild()

    def set_icon_color(
        self, color: QColor, ring: QColor | None = None
    ) -> None:
        """Glyph color for the icons (dash / end / align previews,
        section glyphs) and the swatch ring. Pre-rasterized, so the body
        is rebuilt -- same contract as ToolRail.set_colors (Discussion
        #1, item 8: icons used to vanish in the dark theme)."""
        self._icon_color = QColor(color)
        self._ring_color = QColor(ring if ring is not None else color)
        self._rebuild()

    def set_defaults(self, color: QColor, stroke: float) -> None:
        """Drawing defaults shown when nothing is selected."""
        self._default_color = QColor(color)
        self._default_stroke = float(stroke)
        if not self._items:
            self._rebuild()

    def set_actions(self, **actions: QAction) -> None:
        """Arrange buttons mirror these actions (bring_front, send_back,
        format_painter)."""
        self._actions.update(actions)
        self._rebuild()

    def set_unit(self, unit: str) -> None:
        if unit in UNITS and unit != self._unit:
            self._unit = unit
            self._rebuild()

    def unit(self) -> str:
        return self._unit

    def field(self, label: str) -> QWidget:
        """Input widget of the row labelled `label` (KeyError if absent)."""
        return self._fields[label]

    def has_field(self, label: str) -> bool:
        return label in self._fields

    def header_text(self) -> str:
        w = self._fields.get("__title__")
        return w.text() if isinstance(w, QLabel) else ""

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------
    def _clear_body(self) -> None:
        while self._body_layout.count():
            child = self._body_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                # Hide now: deleteLater only runs on the next event-loop
                # pass, and the old form would show through meanwhile.
                w.hide()
                w.deleteLater()
        self._fields = {}

    def _add_row(
        self, section: _Section, label: str, widget: QWidget,
        key: QWidget | None = None,
    ) -> None:
        lbl = QLabel(label)
        lbl.setObjectName("InspectorLabel")
        section.form.addRow(lbl, widget)
        self._fields[label] = key if key is not None else widget

    def _section(self, title: str) -> _Section:
        sec = _Section(title, self._body)
        self._body_layout.addWidget(sec)
        return sec

    def _header(self, glyph: str, color: QColor, title: str, sub: str) -> None:
        box = QFrame(self._body)
        box.setObjectName("InspectorHeader")
        row = QHBoxLayout(box)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(10)
        tile = QLabel(box)
        tile.setObjectName("InspectorKindTile")
        tile.setFixedSize(34, 34)
        tile.setAlignment(Qt.AlignCenter)
        pm = line_pixmap(glyph, color, 40)
        pm.setDevicePixelRatio(2.0)
        tile.setPixmap(pm)
        row.addWidget(tile)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        t = QLabel(title, box)
        t.setObjectName("InspectorTitle")
        s = QLabel(sub, box)
        s.setObjectName("InspectorSubtitle")
        s.setWordWrap(True)
        texts.addWidget(t)
        texts.addWidget(s)
        row.addLayout(texts, 1)
        self._body_layout.addWidget(box)
        self._fields["__title__"] = t

    def _rebuild(self) -> None:
        self._clear_body()
        if not self._items:
            self._build_empty()
            self._body_layout.addStretch(1)
            return

        first = self._items[0]
        types = {type(it) for it in self._items}
        title, sub, glyph = describe(first)
        name = sub or title
        if len(self._items) == 1:
            self._header(glyph, first.color(), name, "1 selected")
        elif len(types) == 1:
            self._header(
                glyph, first.color(), f"{len(self._items)} x {name}",
                f"{len(self._items)} selected",
            )
        else:
            self._header(
                "select", self._icon_color,
                f"{len(self._items)} annotations",
                "Mixed types: common settings only",
            )

        style = self._section("Style")
        self._add_common_rows(style)

        if len(types) == 1:
            cls = next(iter(types))
            if issubclass(cls, RectangleItem):
                shape = self._section("Shape")
                self._add_outline_row(shape)
                self._add_fill_rows(shape)
                self._add_corner_row(shape)
                self._add_label_rows(self._section("Label"))
            elif issubclass(cls, EllipseItem):
                shape = self._section("Shape")
                self._add_fill_rows(shape)
                self._add_label_rows(self._section("Label"))
            elif issubclass(cls, CloudItem):
                shape = self._section("Shape")
                self._add_outline_row(shape)
                self._add_fill_rows(shape)
            elif issubclass(cls, PolygonItem):
                shape = self._section("Shape")
                self._add_closed_row(shape)
                self._add_fill_rows(shape)
            elif issubclass(cls, PolylineItem):
                self._add_closed_row(self._section("Shape"))
            elif issubclass(cls, ArrowItem):
                ends = self._section("Ends")
                self._add_arrow_rows(ends)
                self._add_line_label_rows(ends)
            elif issubclass(cls, LineItem):
                self._add_line_label_rows(self._section("Ends"))
            elif issubclass(cls, CalloutItem):
                text = self._section("Text")
                self._add_text_rows(text)
                self._add_callout_to_arrow_row(text)
            elif issubclass(cls, TextAnnotationItem):
                self._add_text_rows(self._section("Text"))
            elif issubclass(cls, GdtAnnotationItem):
                self._add_gdt_rows(self._section("GD&T frame"))
            elif issubclass(cls, StampItem):
                self._add_stamp_rows(self._section("Stamp"))
            elif issubclass(cls, FreehandItem):
                pass

        if len(self._items) == 1:
            self._add_geometry_section(first)
        self._add_arrange_section()
        self._body_layout.addStretch(1)
        self._add_footer()

    # ------------------------------------------------------------------
    # empty state: defaults for the next annotation + tips
    # ------------------------------------------------------------------
    def _build_empty(self) -> None:
        self._header(
            "select", self._icon_color, "Nothing selected",
            "Settings below apply to the next annotation",
        )
        nxt = self._section("Next annotation")
        swatches = ColorSwatchRow(self._palette, nxt)
        swatches.set_colors(self._icon_color, self._ring_color)
        swatches.set_current(self._default_color)
        swatches.colorPicked.connect(self.colorPicked)
        swatches.editPaletteRequested.connect(self.editPaletteRequested)
        self._add_row(nxt, "Color", swatches)
        stroke = StrokeField(minimum=1, parent=nxt)
        stroke.set_width(self._default_stroke)
        stroke.presetPicked.connect(self.strokePicked)
        stroke.spin.valueChanged.connect(
            lambda v: self.strokePicked.emit(float(v))
        )
        self._add_row(nxt, "Stroke", stroke, key=stroke.spin)

        tips = self._section("Good to know")
        for key, text in _TIPS:
            row = QWidget(tips)
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            k = QLabel(key, row)
            k.setObjectName("Kbd")
            t = QLabel(text, row)
            t.setObjectName("InspectorTip")
            h.addWidget(k)
            h.addWidget(t, 1)
            tips.form.addRow(row)

    # ------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------
    def _add_common_rows(self, sec: _Section) -> None:
        first = self._items[0]
        swatches = ColorSwatchRow(self._palette, sec)
        swatches.set_colors(self._icon_color, self._ring_color)
        swatches.set_current(first.color())
        swatches.colorPicked.connect(self.colorPicked)
        swatches.editPaletteRequested.connect(self.editPaletteRequested)
        self._add_row(sec, "Color", swatches)

        # Stroke: presets + exact value (0 = no border, fill only).
        stroke = StrokeField(minimum=0, parent=sec)
        stroke.set_width(first.stroke())
        stroke.presetPicked.connect(self.strokePicked)
        self._wire_live_prop(stroke.spin, "stroke", transform=float)
        stroke.spin.editingFinished.connect(
            lambda s=stroke.spin: self.strokeCommitted.emit(float(s.value()))
        )
        self._add_row(sec, "Stroke", stroke, key=stroke.spin)

        dash = SegmentedControl(
            [
                (style, "", dash_icon(style, color=self._icon_color), label)
                for style, label in _DASH_LABELS
            ],
            sec,
        )
        dash.set_value(first.dash_style())
        dash.valueChanged.connect(lambda v: self._push_prop("dash_style", v))
        self._add_row(sec, "Line", dash)

    def _add_fill_rows(self, sec: _Section) -> None:
        first = self._items[0]
        row = QWidget(sec)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        cb = QCheckBox("Filled", row)
        cb.setChecked(bool(first.fill_enabled()))
        cb.toggled.connect(
            lambda checked: self._push_prop("fill_enabled", bool(checked))
        )
        h.addWidget(cb)
        fc_btn = _color_button(first.fill_color())
        fc_btn.clicked.connect(
            lambda: self._pick_color("fill_color", first.fill_color(), fc_btn)
        )
        h.addWidget(fc_btn)
        h.addStretch(1)
        self._add_row(sec, "Fill", row, key=cb)
        self._fields["Fill color"] = fc_btn

        spin = QSpinBox()
        spin.setRange(0, 100)
        spin.setSuffix(" %")
        spin.setValue(int(round(first.fill_opacity() * 100)))
        self._wire_live_prop(
            spin, "fill_opacity", transform=lambda v: v / 100.0
        )
        self._add_row(sec, "Fill opacity", spin)

    def _add_corner_row(self, sec: _Section) -> None:
        first = self._items[0]
        spin = QSpinBox()
        spin.setRange(0, 200)
        spin.setSuffix(" px")
        spin.setValue(int(round(first.corner_radius())))
        self._wire_live_prop(spin, "corner_radius", transform=float)
        self._add_row(sec, "Corners", spin)

    def _add_label_rows(self, sec: _Section) -> None:
        first = self._items[0]
        text_edit = QLineEdit()
        text_edit.setPlaceholderText("Text shown inside the shape")
        text_edit.setText(first.text())
        self._wire_live_prop(text_edit, "text", is_line_edit=True)
        self._add_row(sec, "Text", text_edit)

        lbl_size = QSpinBox()
        lbl_size.setRange(4, 96)
        lbl_size.setSuffix(" pt")
        lbl_size.setValue(int(first.label_font_size()))
        self._wire_live_prop(lbl_size, "label_font_size", transform=int)
        self._add_row(sec, "Text size", lbl_size)

    def _add_arrow_rows(self, sec: _Section) -> None:
        first = self._items[0]
        c1 = self._enum_combo(
            _END_LABELS, first.start_end(), icon_for=end_icon
        )
        c1.currentIndexChanged.connect(
            lambda _i, c=c1: self._push_prop("start_end", c.currentData())
        )
        self._add_row(sec, "Start", c1)
        c2 = self._enum_combo(
            _END_LABELS, first.end_end(), icon_for=end_icon
        )
        c2.currentIndexChanged.connect(
            lambda _i, c=c2: self._push_prop("end_end", c.currentData())
        )
        self._add_row(sec, "End", c2)

    # ------------------------------------------------------------------
    # kind-variant rows (Discussion #1, item 3: merged tools)
    # ------------------------------------------------------------------
    def _add_outline_row(self, sec: _Section) -> None:
        """Rect-footprint shapes: straight border <-> revision cloud."""
        if len(self._items) != 1:
            return
        first = self._items[0]
        seg = SegmentedControl(
            [
                (False, "Straight", line_icon("rectangle", self._icon_color), "Straight outline"),
                (True, "Cloud", line_icon("cloud", self._icon_color), "Revision cloud outline"),
            ],
            sec,
        )
        seg.set_value(isinstance(first, CloudItem))
        seg.valueChanged.connect(
            lambda v, it=first: self._on_outline_changed(it, bool(v))
        )
        self._add_row(sec, "Outline", seg)

    def _add_closed_row(self, sec: _Section) -> None:
        """Multi-vertex paths: open polyline <-> closed polygon."""
        if len(self._items) != 1:
            return
        first = self._items[0]
        seg = SegmentedControl(
            [
                (False, "Open", line_icon("polyline", self._icon_color), "Open path"),
                (True, "Closed", line_icon("polygon", self._icon_color), "Closed shape"),
            ],
            sec,
        )
        seg.set_value(isinstance(first, PolygonItem))
        seg.valueChanged.connect(
            lambda v, it=first: self._on_closed_changed(it, bool(v))
        )
        self._add_row(sec, "Path", seg)

    def _add_line_label_rows(self, sec: _Section) -> None:
        """Lines/arrows carry an optional text label at EACH end,
        rendered just past the endpoint. Labels live on the line itself
        (not a converted callout), so they coexist with bend points."""
        first = self._items[0]
        for prop, border_prop, title, border_title in (
            ("start_label", "start_label_border", "Start label",
             "Start frame"),
            ("end_label", "end_label_border", "End label", "End frame"),
        ):
            edit = QLineEdit()
            edit.setPlaceholderText("Optional")
            edit.setText(str(getattr(first, prop)()))
            self._wire_live_prop(edit, prop, is_line_edit=True, transform=str)
            self._add_row(sec, title, edit)
            combo = self._enum_combo(
                TEXT_BORDER_LABELS, getattr(first, border_prop)()
            )
            combo.currentIndexChanged.connect(
                lambda _i, c=combo, bp=border_prop: self._push_prop(
                    bp, c.currentData()
                )
            )
            self._add_row(sec, border_title, combo)

    def _add_callout_to_arrow_row(self, sec: _Section) -> None:
        if len(self._items) != 1:
            return
        first = self._items[0]
        btn = QPushButton("Convert to arrow (drop text)")
        btn.clicked.connect(
            lambda _checked=False, it=first: self._push_replace(
                it, callout_to_arrow(it)
            )
        )
        self._add_row(sec, "Kind", btn)

    def _add_text_rows(self, sec: _Section) -> None:
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
        self._add_row(sec, "Font", family)

        size = QSpinBox()
        size.setRange(4, 144)
        size.setSuffix(" pt")
        size.setValue(int(first.font_size()))
        self._wire_live_prop(size, "font_size", transform=int)
        self._add_row(sec, "Size", size)

        styles = QWidget(sec)
        h = QHBoxLayout(styles)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        for prop, text, tip in (("bold", "B", "Bold"), ("italic", "I", "Italic")):
            b = QToolButton(styles)
            b.setObjectName("StyleToggle")
            b.setText(text)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setCheckable(True)
            b.setChecked(bool(getattr(first, prop)()))
            font = b.font()
            font.setBold(prop == "bold")
            font.setItalic(prop == "italic")
            b.setFont(font)
            b.toggled.connect(
                lambda checked, p=prop: self._push_prop(p, bool(checked))
            )
            h.addWidget(b)
            self._fields[tip] = b
        h.addStretch(1)
        self._add_row(sec, "Style", styles)

        align = SegmentedControl(
            [
                (value, "", align_icon(value, color=self._icon_color), label)
                for value, label in _ALIGN_LABELS
            ],
            sec,
        )
        align.set_value(first.align())
        align.valueChanged.connect(lambda v: self._push_prop("align", v))
        self._add_row(sec, "Align", align)

        border = self._enum_combo(TEXT_BORDER_LABELS, first.border())
        border.currentIndexChanged.connect(
            lambda _i, c=border: self._push_prop("border", c.currentData())
        )
        self._add_row(sec, "Border", border)

    def _add_stamp_rows(self, sec: _Section) -> None:
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
        self._add_row(sec, "Preset", preset)

        text_edit = QLineEdit()
        text_edit.setText(first.text())
        self._wire_live_prop(text_edit, "text", is_line_edit=True)
        self._add_row(sec, "Text", text_edit)

        size = QSpinBox()
        size.setRange(6, 96)
        size.setSuffix(" pt")
        size.setValue(int(first.font_size()))
        self._wire_live_prop(size, "font_size", transform=int)
        self._add_row(sec, "Size", size)

    def _add_gdt_rows(self, sec: _Section) -> None:
        first = self._items[0]
        size = QSpinBox()
        size.setRange(6, 72)
        size.setSuffix(" pt")
        size.setValue(int(first.font_size()))
        self._wire_live_prop(size, "font_size", transform=int)
        self._add_row(sec, "Frame size", size)
        if len(self._items) == 1:
            edit = QPushButton("Edit frame")
            edit.setIcon(line_icon("pencil", self._icon_color))
            edit.setToolTip("Edit the frame on the page (or double-click it)")
            edit.clicked.connect(self.editRequested)
            self._add_row(sec, "Content", edit)

    # ------------------------------------------------------------------
    # position & size (single item), with a mm / pt switch
    # ------------------------------------------------------------------
    def _to_pt(self, value: float) -> float:
        return value / _MM_PER_PT if self._unit == "mm" else value

    def _from_pt(self, value_pt: float) -> float:
        return value_pt * _MM_PER_PT if self._unit == "mm" else value_pt

    def _unit_spin(
        self, value_pt: float, *, minimum_pt: float = -100000.0,
        maximum_pt: float = 100000.0,
    ) -> QDoubleSpinBox:
        spin = self._geometry_spin(
            self._from_pt(value_pt),
            minimum=self._from_pt(minimum_pt),
            maximum=self._from_pt(maximum_pt),
        )
        spin.setDecimals(2 if self._unit == "mm" else 1)
        spin.setValue(self._from_pt(value_pt))
        spin.setSuffix(f" {self._unit}")
        return spin

    def _add_geometry_section(self, item: AnnotationItem) -> None:
        """Precise numeric position/size, mirroring the live measurement
        HUD shown while dragging. Live-previews on every change (see
        `_wire_live_geom`) and lands as the same undo command a manual
        drag would produce, once."""
        sec = self._section("Position & size")
        units = SegmentedControl(
            [(u, u, None, f"Show positions in {u}") for u in UNITS], sec
        )
        units.set_value(self._unit)
        units.setFixedWidth(96)
        units.valueChanged.connect(lambda u: self.set_unit(str(u)))
        sec.header.addWidget(units)
        self._fields["Unit"] = units

        rect = item_scene_rect(item)
        x_spin = self._unit_spin(px_to_pt(rect.x()))
        self._wire_live_geom(
            x_spin,
            lambda v: self._apply_move_live(item, x=self._to_pt(v)),
            lambda: QPointF(item.pos()),
            lambda orig: self._commit_move(item, orig),
        )
        self._add_row(sec, "X", x_spin)

        y_spin = self._unit_spin(px_to_pt(rect.y()))
        self._wire_live_geom(
            y_spin,
            lambda v: self._apply_move_live(item, y=self._to_pt(v)),
            lambda: QPointF(item.pos()),
            lambda orig: self._commit_move(item, orig),
        )
        self._add_row(sec, "Y", y_spin)

        if isinstance(item, LineItem):
            p1, p2 = item.line_points()
            dx, dy = p2.x() - p1.x(), p2.y() - p1.y()
            length_px = math.hypot(dx, dy)
            angle_deg = (-math.degrees(math.atan2(dy, dx))) % 360.0

            len_spin = self._unit_spin(
                px_to_pt(length_px), minimum_pt=0.1, maximum_pt=100000.0
            )
            self._wire_live_geom(
                len_spin,
                lambda v: self._apply_line_geom_live(
                    item, length_pt=self._to_pt(v)
                ),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            self._add_row(sec, "Length", len_spin)

            ang_spin = self._geometry_spin(
                angle_deg, minimum=-3600.0, maximum=3600.0
            )
            ang_spin.setSuffix(" °")
            self._wire_live_geom(
                ang_spin,
                lambda v: self._apply_line_geom_live(item, angle_deg=v),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            self._add_row(sec, "Angle", ang_spin)
        elif hasattr(item, "rect") and hasattr(item, "set_rect"):
            w_spin = self._unit_spin(
                px_to_pt(rect.width()), minimum_pt=0.1, maximum_pt=100000.0
            )
            self._wire_live_geom(
                w_spin,
                lambda v: self._apply_resize_live(item, w=self._to_pt(v)),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            self._add_row(sec, "Width", w_spin)

            h_spin = self._unit_spin(
                px_to_pt(rect.height()), minimum_pt=0.1, maximum_pt=100000.0
            )
            self._wire_live_geom(
                h_spin,
                lambda v: self._apply_resize_live(item, h=self._to_pt(v)),
                item.geom_snapshot,
                lambda orig: self._commit_resize(item, orig),
            )
            self._add_row(sec, "Height", h_spin)

    # ------------------------------------------------------------------
    # arrange + footer
    # ------------------------------------------------------------------
    def _action_button(self, key: str, text: str, glyph: str) -> QPushButton | None:
        act = self._actions.get(key)
        if act is None:
            return None
        b = QPushButton(text)
        b.setObjectName("InspectorButton")
        b.setIcon(line_icon(glyph, self._icon_color))
        b.setEnabled(act.isEnabled())
        tip = act.toolTip() or text
        b.setToolTip(tip)
        if act.isCheckable():
            b.setCheckable(True)
            b.setChecked(act.isChecked())
            b.clicked.connect(lambda checked: act.setChecked(checked))
        else:
            b.clicked.connect(act.trigger)
        self._fields[text] = b
        return b

    def _add_arrange_section(self) -> None:
        buttons = [
            self._action_button("bring_front", "To front", "to-front"),
            self._action_button("send_back", "To back", "to-back"),
        ]
        painter = (
            self._action_button("format_painter", "Copy style", "brush")
            if len(self._items) == 1
            else None
        )
        if not any(buttons) and painter is None:
            return
        sec = self._section("Arrange")
        row = QWidget(sec)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        for b in buttons:
            if b is not None:
                h.addWidget(b, 1)
        sec.form.addRow(row)
        if painter is not None:
            sec.form.addRow(painter)

    def _add_footer(self) -> None:
        box = QFrame(self._body)
        box.setObjectName("InspectorFooter")
        h = QHBoxLayout(box)
        h.setContentsMargins(14, 10, 14, 12)
        h.setSpacing(8)
        dup = QPushButton("Duplicate", box)
        dup.setObjectName("InspectorButton")
        dup.setIcon(line_icon("duplicate", self._icon_color))
        dup.setToolTip("Duplicate (Ctrl+D)")
        dup.clicked.connect(self.duplicateRequested)
        dele = QPushButton("Delete", box)
        dele.setObjectName("DangerButton")
        dele.setToolTip("Delete (Del)")
        dele.clicked.connect(self.deleteRequested)
        h.addWidget(dup, 1)
        h.addWidget(dele, 1)
        self._fields["Duplicate"] = dup
        self._fields["Delete"] = dele
        self._body_layout.addWidget(box)

    # ------------------------------------------------------------------
    # geometry helpers (values in PDF points)
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # conversions and presets
    # ------------------------------------------------------------------
    def _push_replace(self, old: AnnotationItem, new: AnnotationItem) -> None:
        """Swap `old` for its converted variant, as one undo step."""
        scene = old.scene()
        parent_item = old.parentItem()
        if scene is None or parent_item is None:
            return
        cmd = ReplaceAnnotationCommand(scene, parent_item, old, new)
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()

    def _on_outline_changed(self, item, cloudy: bool) -> None:  # noqa: ANN001
        converted = convert_shape_outline(item, cloudy)
        if converted is not None:
            self._push_replace(item, converted)

    def _on_closed_changed(self, item, closed: bool) -> None:  # noqa: ANN001
        converted = convert_poly_closed(item, closed)
        if converted is not None:
            self._push_replace(item, converted)

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
        icon_color = self._icon_color
        for value, label in entries:
            if icon_for is not None:
                combo.addItem(icon_for(value, color=icon_color), label, value)
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
        def apply(c: QColor) -> None:
            if not c.isValid():
                return
            self._push_prop(prop, QColor(c))
            button.setStyleSheet(
                f"background: {c.name()}; border: 1px solid #888;"
            )

        popup_color_picker(
            self,
            initial,
            apply,
            global_pos=button.mapToGlobal(button.rect().bottomLeft()),
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
