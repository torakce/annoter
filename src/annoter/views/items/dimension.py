"""DimensionItem: a linear dimension, as on a technical drawing
(2026-09-28).

Two points on the drawing, a dimension line parallel to what is
measured, one extension line from each point to it (ISO 129-1: a small
gap at the feature, a short overshoot past the dimension line),
arrowheads (or oblique strokes, or dots) where the dimension line meets
the extension lines, and the value above the dimension line, parallel
to it and readable from the bottom or the right of the sheet.

    p1, p2        the measured points (item coordinates)
    orientation   ALIGNED: the true distance; HORIZONTAL / VERTICAL:
                  its projection (the dimension line is then horizontal
                  / vertical whatever the points)
    offset        where the dimension line runs: its signed distance
                  from p1 along the normal `n` of the measured direction
    shift         the value's position along the dimension line, from
                  its middle (0: centered)

The value is a `SubTextItem` (like the notes of a GD&T frame), so it
takes symbols, prefixes and inline tolerances like any text. Its runs
are stored with a placeholder run `{"value": 1}` standing for the
measured length (mm on the sheet, `decimals` places): the number
follows the geometry. Editing the text keeps that link as long as the
measured number is still in it ("Ø25.4 ±0.1" stays live); a text whose
number was changed by hand is an override and stays as typed until
"Use measured value".

Sizes (arrows, gaps) are proportional to the value's text height, so a
bigger text gives a bigger dimension, as on a drawing.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QGraphicsItem

from annoter.config import BASE_RENDER_DPI
from annoter.model.styles import DimOrientation, EndStyle, HandleRole
from annoter.views.items.base import HANDLE_COLOR, HANDLE_HALF, AnnotationItem
from annoter.views.items.sub_text import SubTextItem

# Placeholder run: the measured value, formatted.
VALUE_RUN = {"value": 1}
# Handle on the dimension line under the value: moves the line (and
# the value along it).
TEXT_HANDLE = "dim-text"

MM_PER_PX = 25.4 / BASE_RENDER_DPI  # item units are page pixels

# Proportions, in text heights of the value (ISO 129-1 / 128 spirit).
ARROW = 0.7  # arrowhead length
EXT_GAP = 0.2  # gap between the feature and its extension line
EXT_OVER = 0.45  # extension line past the dimension line
TEXT_GAP = 0.3  # between the dimension line and the value's ink
OUTSIDE_TAIL = 1.6  # dimension line past an outside arrow, in arrows
ARROW_HALF_ANGLE = math.radians(15.0)  # 30-degree closed arrowheads
DOT_RADIUS = 0.18  # dot end, in arrow lengths

SHIFT_MAGNET_PX = 8.0  # screen pixels: the value snaps back to center
HIGHLIGHT_PX = 4.0  # selection band, screen pixels each side
HIGHLIGHT_ALPHA = 90

HANDLE_TIPS = {
    HandleRole.P1: "First point",
    HandleRole.P2: "Second point",
    TEXT_HANDLE: (
        "Dimension line: drag to move it and the value along it "
        "(Shift keeps the value in place)"
    ),
}


# ----------------------------------------------------------------------
# value text
# ----------------------------------------------------------------------
def format_mm(value: float, decimals: int) -> str:
    """`value` rounded half up to `decimals` places, trailing zeros
    dropped: 25.40 -> "25.4", 30.0 -> "30"."""
    decimals = max(0, min(3, int(decimals)))
    q = Decimal(repr(abs(float(value)))).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
    )
    text = format(q, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _number_pattern(text: str) -> re.Pattern:
    """`text` as a whole number: not glued to other digits ("5" is not
    found in "25" nor in "5.5")."""
    return re.compile(
        r"(?<![\d.,])" + re.escape(text) + r"(?!\d)(?![.,]\d)"
    )


def _merge_text_runs(runs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for run in runs:
        if "t" in run and out and "t" in out[-1]:
            out[-1] = {"t": out[-1]["t"] + run["t"]}
        elif "t" in run and not run["t"]:
            continue
        else:
            out.append(dict(run))
    return out


# ----------------------------------------------------------------------
# extremities as plain shapes (painted on screen, written to the PDF)
# ----------------------------------------------------------------------
Primitive = tuple


def end_primitives(
    anchor: QPointF, towards: QPointF, style: EndStyle, size: float
) -> list[Primitive]:
    """The extremity `style` at `anchor`, its body towards `towards`:
    ("fill", [points]) polygons, ("stroke", [points]) polylines and
    ("dot", center, radius) discs."""
    dx, dy = towards.x() - anchor.x(), towards.y() - anchor.y()
    length = math.hypot(dx, dy)
    if length < 1e-9 or size <= 0.0:
        return []
    ux, uy = dx / length, dy / length
    if style in (EndStyle.CLOSED_ARROW, EndStyle.OPEN_ARROW):
        c, s = math.cos(ARROW_HALF_ANGLE), math.sin(ARROW_HALF_ANGLE)
        h1 = QPointF(
            anchor.x() + size * (ux * c - uy * s),
            anchor.y() + size * (ux * s + uy * c),
        )
        h2 = QPointF(
            anchor.x() + size * (ux * c + uy * s),
            anchor.y() + size * (-ux * s + uy * c),
        )
        if style is EndStyle.CLOSED_ARROW:
            return [("fill", [QPointF(anchor), h1, h2])]
        return [("stroke", [h1, QPointF(anchor), h2])]
    if style is EndStyle.SLASH:
        # Oblique stroke at 45 degrees to the dimension line, centered
        # on the intersection (symmetric, so both ends lean alike).
        half = size * 0.5
        ax, ay = (ux + uy) * math.sqrt(0.5), (uy - ux) * math.sqrt(0.5)
        return [
            (
                "stroke",
                [
                    QPointF(anchor.x() - ax * half, anchor.y() - ay * half),
                    QPointF(anchor.x() + ax * half, anchor.y() + ay * half),
                ],
            )
        ]
    if style is EndStyle.CIRCLE:
        return [("dot", QPointF(anchor), size * DOT_RADIUS)]
    return []


def primitive_points(prim: Primitive) -> list[QPointF]:
    if prim[0] == "dot":
        c, r = prim[1], prim[2]
        return [QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r)]
    return list(prim[1])


@dataclass
class DimGeometry:
    """Everything drawn, in item coordinates (see `_layout`)."""

    d1: QPointF
    d2: QPointF
    direction: QPointF  # unit, d1 -> d2
    dim_line: tuple[QPointF, QPointF]
    ext_lines: list[tuple[QPointF, QPointF]] = field(default_factory=list)
    ends: list[tuple[QPointF, QPointF]] = field(default_factory=list)
    center: QPointF = field(default_factory=QPointF)  # under the value
    angle: float = 0.0  # the value's rotation, degrees
    arrow: float = 0.0
    inside: bool = True


class DimensionItem(AnnotationItem):
    """Linear dimension between two points (see module docstring)."""

    KIND = "dimension"

    def __init__(
        self,
        p1: QPointF,
        p2: QPointF,
        offset: float = 0.0,
        orientation: DimOrientation = DimOrientation.ALIGNED,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._p1 = QPointF(p1)
        self._p2 = QPointF(p2)
        self._offset = float(offset)
        self._shift = 0.0
        self._orientation = orientation
        self._end_style = EndStyle.CLOSED_ARROW
        self._decimals = 1
        self._runs: list[dict] = [dict(VALUE_RUN)]
        self._geo: DimGeometry | None = None
        self._syncing = False
        self._label = SubTextItem(self, "value")
        self._label.set_color(self._color)
        self._label._inner.document().contentsChanged.connect(
            self._on_label_changed
        )
        self._sync_label()

    # ------------------------------------------------------------------
    # measured points and placement
    # ------------------------------------------------------------------
    def points(self) -> tuple[QPointF, QPointF]:
        return QPointF(self._p1), QPointF(self._p2)

    def set_points(self, p1: QPointF, p2: QPointF) -> None:
        self._p1, self._p2 = QPointF(p1), QPointF(p2)
        self._sync_label()

    def offset(self) -> float:
        return self._offset

    def set_offset(self, offset: float) -> None:
        self._offset = float(offset)
        self._layout()

    def shift(self) -> float:
        return self._shift

    def set_shift(self, shift: float) -> None:
        self._shift = float(shift)
        self._layout()

    def orientation(self) -> DimOrientation:
        return self._orientation

    def set_orientation(self, orientation: DimOrientation) -> None:
        """Raw setter (the offset keeps its value); interactive changes
        go through `switch_orientation`."""
        if orientation is self._orientation:
            return
        self._orientation = orientation
        self._sync_label()

    def axes(self) -> tuple[QPointF, QPointF]:
        """(u, n): the measured direction and the normal the offset is
        counted along, both unit vectors."""
        if self._orientation is DimOrientation.HORIZONTAL:
            u = QPointF(1.0, 0.0)
        elif self._orientation is DimOrientation.VERTICAL:
            u = QPointF(0.0, 1.0)
        else:
            dx = self._p2.x() - self._p1.x()
            dy = self._p2.y() - self._p1.y()
            length = math.hypot(dx, dy)
            u = (
                QPointF(dx / length, dy / length)
                if length > 1e-9
                else QPointF(1.0, 0.0)
            )
        return u, QPointF(u.y(), -u.x())

    def feet(self) -> tuple[QPointF, QPointF]:
        """Where the extension lines meet the dimension line."""
        _u, n = self.axes()
        a2 = (self._p2.x() - self._p1.x()) * n.x() + (
            self._p2.y() - self._p1.y()
        ) * n.y()
        k1, k2 = self._offset, self._offset - a2
        return (
            QPointF(self._p1.x() + n.x() * k1, self._p1.y() + n.y() * k1),
            QPointF(self._p2.x() + n.x() * k2, self._p2.y() + n.y() * k2),
        )

    def offset_through(self, local: QPointF) -> float:
        """The offset putting the dimension line through `local`."""
        _u, n = self.axes()
        return (local.x() - self._p1.x()) * n.x() + (
            local.y() - self._p1.y()
        ) * n.y()

    def place_through(
        self, local: QPointF, orientation: DimOrientation | None = None
    ) -> None:
        """Run the dimension line through `local` (placement click),
        optionally changing the orientation; the value re-centers."""
        if orientation is not None:
            self._orientation = orientation
        self._offset = self.offset_through(local)
        self._shift = 0.0
        self._sync_label()

    def switch_orientation(self, orientation: DimOrientation) -> None:
        """Change what is measured, keeping the dimension line where it
        was when it can stay there, else moving it just outside the
        points on the side it was on."""
        if orientation is self._orientation:
            return
        d1, d2 = self.feet()
        mid = QPointF((d1.x() + d2.x()) / 2.0, (d1.y() + d2.y()) / 2.0)
        clearance = max(
            math.hypot(d1.x() - self._p1.x(), d1.y() - self._p1.y()),
            math.hypot(d2.x() - self._p2.x(), d2.y() - self._p2.y()),
            2.0 * self._text_height(),
        )
        self._orientation = orientation
        level = self.offset_through(mid)
        _u, n = self.axes()
        a2 = (self._p2.x() - self._p1.x()) * n.x() + (
            self._p2.y() - self._p1.y()
        ) * n.y()
        lo, hi = min(0.0, a2), max(0.0, a2)
        if lo - 1e-6 < level < hi + 1e-6 and hi - lo > 1e-6:
            level = (
                lo - clearance if level - lo < hi - level else hi + clearance
            )
        self._offset = level
        self._shift = 0.0
        self._sync_label()

    def measured_length(self) -> float:
        """The measured length, item units."""
        d1, d2 = self.feet()
        return math.hypot(d2.x() - d1.x(), d2.y() - d1.y())

    def value_mm(self) -> float:
        """The measured length in millimeters on the sheet."""
        return self.measured_length() * MM_PER_PX

    # ------------------------------------------------------------------
    # value text
    # ------------------------------------------------------------------
    def decimals(self) -> int:
        return self._decimals

    def set_decimals(self, decimals: int) -> None:
        d = max(0, min(3, int(decimals)))
        if d == self._decimals:
            return
        self._decimals = d
        self._sync_label()

    def value_text(self) -> str:
        return format_mm(self.value_mm(), self._decimals)

    def value_runs(self) -> list[dict]:
        return [dict(r) for r in self._runs]

    def set_value_runs(self, runs: list[dict]) -> None:
        clean = _merge_text_runs(
            [
                dict(VALUE_RUN) if "value" in r else dict(r)
                for r in runs
                if isinstance(r, dict)
            ]
        )
        self._runs = clean or [dict(VALUE_RUN)]
        self._sync_label()

    def is_measured(self) -> bool:
        """True when the text shows the measured number (it follows the
        points), False for a text overridden by hand."""
        return any("value" in r for r in self._runs)

    def display_runs(self) -> list[dict]:
        value = self.value_text()
        return _merge_text_runs(
            [{"t": value} if "value" in r else dict(r) for r in self._runs]
        )

    def runs_from_label(self, label_runs: list[dict]) -> list[dict]:
        """Stored runs for text edited in the value: the measured number,
        if still there, becomes the placeholder again; an empty text
        falls back to the measured value alone."""
        blank = not any(
            ("t" in r and r["t"].strip()) or "tol" in r for r in label_runs
        )
        if blank:
            return [dict(VALUE_RUN)]
        pattern = _number_pattern(self.value_text())
        out: list[dict] = []
        found = False
        for run in label_runs:
            if not found and "t" in run:
                m = pattern.search(run["t"])
                if m is not None:
                    found = True
                    before, after = run["t"][: m.start()], run["t"][m.end():]
                    if before:
                        out.append({"t": before})
                    out.append(dict(VALUE_RUN))
                    if after:
                        out.append({"t": after})
                    continue
            out.append(dict(run))
        return _merge_text_runs(out)

    def text(self) -> str:
        """Plain text of the value as shown (annotation list, search)."""
        return self._label.text()

    def label(self) -> str:
        return f"Dimension {self.text()}".strip()

    def label_item(self) -> SubTextItem:
        return self._label

    # ------------------------------------------------------------------
    # style
    # ------------------------------------------------------------------
    def end_style(self) -> EndStyle:
        return self._end_style

    def set_end_style(self, style: EndStyle) -> None:
        if style is self._end_style:
            return
        self._end_style = style
        self._layout()

    def font_size(self) -> int:
        return self._label.font_size()

    def set_font_size(self, size: int) -> None:
        self._label.set_font_size(max(4, int(size)))
        self._layout()

    def set_color(self, color: QColor) -> None:
        super().set_color(color)
        self._label.set_color(color)

    def set_stroke(self, width: float) -> None:
        super().set_stroke(width)
        self._layout()

    # ------------------------------------------------------------------
    # editing the value
    # ------------------------------------------------------------------
    def begin_text_edit(self) -> None:
        self._label.begin_edit()

    def begin_edit(self) -> None:
        self._label.begin_edit()

    def start_typing(self, text: str) -> None:
        self._label.start_typing(text)

    def is_editing(self) -> bool:
        return self._label.is_editing()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802
        self.begin_text_edit()
        event.accept()

    def _on_label_changed(self) -> None:
        # Typing in the value keeps it centered on the line.
        if not self._syncing:
            self._layout()

    def _sync_label(self) -> None:
        """Show the stored runs (measured number filled in), unless the
        value is being edited: then the text is the user's until the
        session ends."""
        if not self._label.is_editing():
            runs = self.display_runs()
            if self._label.rich_runs() != runs:
                self._syncing = True
                try:
                    self._label.set_rich_runs(runs)
                finally:
                    self._syncing = False
        self._layout()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _text_height(self) -> float:
        return QFontMetricsF(self._label._inner.font()).height()

    def geometry(self) -> DimGeometry:
        if self._geo is None:
            self._layout()
        return self._geo

    def _layout(self) -> None:
        self.prepareGeometryChange()
        t = self._text_height()
        arrow = ARROW * t
        d1, d2 = self.feet()
        wx, wy = d2.x() - d1.x(), d2.y() - d1.y()
        length = math.hypot(wx, wy)
        if length > 1e-9:
            w = QPointF(wx / length, wy / length)
        else:
            w, _n = self.axes()

        # The value reads from the bottom or from the right of the sheet
        # (ISO 129-1): along the line, turned so its top faces up or
        # left; lines falling steeply to the right (the 30-degree zone
        # left of vertical) read upwards.
        angle = math.degrees(math.atan2(w.y(), w.x()))
        if angle >= 60.0 - 1e-6:
            angle -= 180.0
        elif angle < -120.0 - 1e-6:
            angle += 180.0
        rad = math.radians(angle)
        up = QPointF(math.sin(rad), -math.cos(rad))
        s_center = length / 2.0 + self._shift
        center = QPointF(d1.x() + w.x() * s_center, d1.y() + w.y() * s_center)

        lab = self._label
        rect = lab.content_rect()
        _blank_top, blank_bottom = lab.ink_margins()
        anchor = QPointF(rect.center().x(), rect.bottom() - blank_bottom)
        target = QPointF(
            center.x() + up.x() * TEXT_GAP * t,
            center.y() + up.y() * TEXT_GAP * t,
        )
        lab.setRotation(angle)
        c, s = math.cos(rad), math.sin(rad)
        lab.setPos(
            target.x() - (anchor.x() * c - anchor.y() * s),
            target.y() - (anchor.x() * s + anchor.y() * c),
        )

        # Arrowheads go outside when they do not fit between the
        # extension lines; strokes and dots always sit on them.
        heads = self._end_style in (EndStyle.CLOSED_ARROW, EndStyle.OPEN_ARROW)
        inside = not heads or length >= 2.0 * arrow + 0.5 * t
        lo, hi = 0.0, length
        if not inside:
            lo -= arrow * OUTSIDE_TAIL
            hi += arrow * OUTSIDE_TAIL
        # The dimension line runs on under a value moved past its ends.
        margin = lab._inner.document().documentMargin()
        half_text = max(0.0, rect.width() / 2.0 - margin)
        lo = min(lo, s_center - half_text)
        hi = max(hi, s_center + half_text)
        dim_line = (
            QPointF(d1.x() + w.x() * lo, d1.y() + w.y() * lo),
            QPointF(d1.x() + w.x() * hi, d1.y() + w.y() * hi),
        )
        if inside:
            ends = [
                (QPointF(d1), QPointF(d1.x() + w.x(), d1.y() + w.y())),
                (QPointF(d2), QPointF(d2.x() - w.x(), d2.y() - w.y())),
            ]
        else:
            ends = [
                (QPointF(d1), QPointF(d1.x() - w.x(), d1.y() - w.y())),
                (QPointF(d2), QPointF(d2.x() + w.x(), d2.y() + w.y())),
            ]

        ext_lines: list[tuple[QPointF, QPointF]] = []
        gap, over = EXT_GAP * t, EXT_OVER * t
        for p, d in ((self._p1, d1), (self._p2, d2)):
            ex, ey = d.x() - p.x(), d.y() - p.y()
            le = math.hypot(ex, ey)
            if le <= gap + 1e-6:
                continue  # the dimension line runs on the feature
            ux, uy = ex / le, ey / le
            ext_lines.append(
                (
                    QPointF(p.x() + ux * gap, p.y() + uy * gap),
                    QPointF(d.x() + ux * over, d.y() + uy * over),
                )
            )

        self._geo = DimGeometry(
            d1=d1,
            d2=d2,
            direction=w,
            dim_line=dim_line,
            ext_lines=ext_lines,
            ends=ends,
            center=center,
            angle=angle,
            arrow=arrow,
            inside=inside,
        )
        self.update()

    def end_shapes(self) -> list[Primitive]:
        geo = self.geometry()
        prims: list[Primitive] = []
        for anchor, towards in geo.ends:
            prims.extend(
                end_primitives(anchor, towards, self._end_style, geo.arrow)
            )
        return prims

    def line_segments(self) -> list[tuple[QPointF, QPointF]]:
        """Extension lines and the dimension line, item coordinates."""
        geo = self.geometry()
        return [*geo.ext_lines, geo.dim_line]

    def label_polygon(self) -> QPolygonF:
        """The value's frame, item coordinates (it may be rotated)."""
        return self._label.mapToParent(QPolygonF(self._label.content_rect()))

    def content_rect(self) -> QRectF:
        """What is drawn (value included), without chrome."""
        pts: list[QPointF] = [QPointF(self._p1), QPointF(self._p2)]
        for a, b in self.line_segments():
            pts += [a, b]
        for prim in self.end_shapes():
            pts += primitive_points(prim)
        pts += list(self.label_polygon())
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def boundingRect(self) -> QRectF:  # noqa: N802
        m = self._stroke / 2.0 + 2.0
        if self.isSelected():
            m = max(
                m,
                self.handles_extent(),
                self._stroke / 2.0 + HIGHLIGHT_PX * self.screen_px() + 1.0,
            )
        return self.content_rect().adjusted(-m, -m, m, m)

    def _lines_path(self) -> QPainterPath:
        path = QPainterPath()
        for a, b in self.line_segments():
            path.moveTo(a)
            path.lineTo(b)
        return path

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self._stroke, 10.0 * self.screen_px()))
        shape = stroker.createStroke(self._lines_path())
        shape.setFillRule(Qt.WindingFill)
        for prim in self.end_shapes():
            if prim[0] == "dot":
                shape.addEllipse(prim[1], prim[2], prim[2])
            else:
                shape.addPolygon(QPolygonF(prim[1]))
        shape.addPolygon(self.label_polygon())
        if self.isSelected():
            for pt in self.handle_positions().values():
                shape.addRect(self._handle_hit_rect(pt))
        return shape

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def _pen(self) -> QPen:
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.MiterJoin)
        return self._apply_dash(pen)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        lines = self._lines_path()
        painter.setBrush(Qt.NoBrush)
        if self.isSelected():
            band_color = QColor(HANDLE_COLOR)
            band_color.setAlpha(HIGHLIGHT_ALPHA)
            band = QPen(
                band_color,
                self._stroke + 2.0 * HIGHLIGHT_PX * self.screen_px(),
            )
            band.setCapStyle(Qt.RoundCap)
            painter.setPen(band)
            painter.drawPath(lines)
        painter.setPen(self._pen())
        painter.drawPath(lines)
        solid = QPen(self._color, self._stroke)
        solid.setJoinStyle(Qt.MiterJoin)
        for prim in self.end_shapes():
            if prim[0] == "fill":
                painter.setPen(Qt.NoPen)
                painter.setBrush(self._color)
                painter.drawPolygon(QPolygonF(prim[1]))
            elif prim[0] == "stroke":
                painter.setPen(solid)
                painter.setBrush(Qt.NoBrush)
                painter.drawPolyline(QPolygonF(prim[1]))
            elif prim[0] == "dot":
                painter.setPen(Qt.NoPen)
                painter.setBrush(self._color)
                painter.drawEllipse(prim[1], prim[2], prim[2])
        if self.isSelected():
            self._draw_handles(painter)

    def _draw_handles(self, painter) -> None:  # noqa: ANN001
        half = HANDLE_HALF * self.screen_px()
        painter.save()
        outline = QPen(HANDLE_COLOR, 0)
        outline.setCosmetic(True)
        painter.setPen(outline)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRect(self._handle_visual_rect(self.geometry().center))
        ring = QPen(QColor("#FFFFFF"), 1.5)
        ring.setCosmetic(True)
        painter.setPen(ring)
        painter.setBrush(HANDLE_COLOR)
        for p in (self._p1, self._p2):
            painter.drawEllipse(p, half, half)
        painter.restore()

    def hoverMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        role = self.hit_handle(event.pos()) if self.isSelected() else None
        self.setToolTip(HANDLE_TIPS.get(role, ""))
        super().hoverMoveEvent(event)

    # ------------------------------------------------------------------
    # handles
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict:
        return {
            HandleRole.P1: QPointF(self._p1),
            HandleRole.P2: QPointF(self._p2),
            TEXT_HANDLE: QPointF(self.geometry().center),
        }

    def apply_resize(self, role, local_pos: QPointF) -> None:  # noqa: ANN001
        if role in (HandleRole.P1, HandleRole.P2):
            self._move_point(role, local_pos)
        elif role == TEXT_HANDLE:
            self._drag_line(local_pos)

    def _move_point(self, role, local_pos: QPointF) -> None:  # noqa: ANN001
        # A horizontal / vertical dimension line stays where it is; an
        # aligned one keeps its distance to the points it measures.
        keep = None
        if self._orientation is not DimOrientation.ALIGNED:
            keep = self.feet()[0]
        if role is HandleRole.P1:
            self._p1 = QPointF(local_pos)
        else:
            self._p2 = QPointF(local_pos)
        if keep is not None:
            self._offset = self.offset_through(keep)
        self._sync_label()

    def _drag_line(self, local_pos: QPointF) -> None:
        self._offset = self.offset_through(local_pos)
        d1, d2 = self.feet()
        wx, wy = d2.x() - d1.x(), d2.y() - d1.y()
        length = math.hypot(wx, wy)
        if length > 1e-9:
            mid = QPointF((d1.x() + d2.x()) / 2.0, (d1.y() + d2.y()) / 2.0)
            shift = (
                (local_pos.x() - mid.x()) * wx + (local_pos.y() - mid.y()) * wy
            ) / length
            if abs(shift) <= SHIFT_MAGNET_PX * self.screen_px():
                shift = 0.0
            self._shift = shift
        self._layout()

    def constrain_line_drag(self, local_pos: QPointF) -> QPointF:
        """Shift on the line handle: only the offset changes, the value
        keeps its place along the line."""
        center = self.geometry().center
        _u, n = self.axes()
        k = (local_pos.x() - center.x()) * n.x() + (
            local_pos.y() - center.y()
        ) * n.y()
        return QPointF(center.x() + n.x() * k, center.y() + n.y() * k)

    def geom_snapshot(self) -> object:
        return (
            QPointF(self.pos()),
            QPointF(self._p1),
            QPointF(self._p2),
            float(self._offset),
            float(self._shift),
            self._orientation,
        )

    def apply_geom(self, snapshot: object) -> None:
        if not (isinstance(snapshot, tuple) and len(snapshot) == 6):
            return
        pos, p1, p2, offset, shift, orientation = snapshot
        self.setPos(pos)
        self._p1, self._p2 = QPointF(p1), QPointF(p2)
        self._offset, self._shift = float(offset), float(shift)
        self._orientation = orientation
        self._sync_label()

    def scale_geometry(self, s: float) -> None:
        super().scale_geometry(s)
        self._p1 = QPointF(self._p1.x() * s, self._p1.y() * s)
        self._p2 = QPointF(self._p2.x() * s, self._p2.y() * s)
        self._offset *= s
        self._shift *= s
        self._label.set_font_size(max(4, round(self._label.font_size() * s)))
        self._sync_label()

    # ------------------------------------------------------------------
    # duplication
    # ------------------------------------------------------------------
    def clone(self) -> "DimensionItem":
        c = DimensionItem(self._p1, self._p2, self._offset, self._orientation)
        self._copy_base_style_into(c)
        c._shift = self._shift
        c._end_style = self._end_style
        c._decimals = self._decimals
        c._label.set_font_size(self._label.font_size())
        c.set_value_runs(self.value_runs())
        return c
