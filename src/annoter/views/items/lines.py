"""Line and Arrow annotation items.

Arrow line-endings are configurable on both ends via the EndStyle enum;
this lets the user draw arrows in either direction or two-headed arrows.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from annoter.model.styles import EndStyle, HandleRole, TextBorder
from annoter.views.items.base import (
    HANDLE_COLOR,
    HANDLE_HALF,
    HANDLE_HIT_HALF,
    AnnotationItem,
)
from annoter.views.items.rounding import has_rounding, rounded_path
from annoter.views.items.line_ends import (
    HEAD_HALF_ANGLE,
    bend_point_on_segment,
    draw_line_end,
)
from annoter.views.items.text import circumscribed_circle_rect

_LABEL_FONT_FAMILY = "Helvetica"
_LABEL_POINT_SIZE = 11
_LABEL_PADDING = 4.0

# Selected-line chrome (Lot K), in screen pixels: the START handle is a
# disc, the END handle a square, bends are small hollow discs, and a
# chevron in the middle of the longest segment points from start to end.
_CHEVRON_HALF_PX = 4.0
_CHEVRON_MIN_PATH_PX = 48.0  # no chevron on a line shorter than this
HANDLE_TIPS = {
    HandleRole.P1: "Start point",
    HandleRole.P2: "End point",
}


class LineItem(AnnotationItem):
    KIND = "line"

    def __init__(
        self,
        p1: QPointF,
        p2: QPointF,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._p1: QPointF = QPointF(p1)
        self._p2: QPointF = QPointF(p2)
        # Optional intermediate bend points between p1 and p2, in path
        # order (Discussion #1, item 5). Each bend is a draggable handle;
        # right-click adds/removes them.
        self._bends: list[QPointF] = []
        # One corner radius per bend (0 = sharp), 2026-09-28.
        self._bend_radii: list[float] = []
        # Optional text labels floating just past each endpoint. Carried
        # by the line itself (not a converted callout) so labels and
        # bends coexist and BOTH ends can be labeled.
        self._start_label: str = ""
        self._end_label: str = ""
        # Optional outline around each label, independently (box for a
        # GD&T datum letter at one end, nothing or a circle at the
        # other).
        self._start_label_border: TextBorder = TextBorder.NONE
        self._end_label_border: TextBorder = TextBorder.NONE

    def line_points(self) -> tuple[QPointF, QPointF]:
        return QPointF(self._p1), QPointF(self._p2)

    def set_line_points(self, p1: QPointF, p2: QPointF) -> None:
        self.prepareGeometryChange()
        self._p1 = QPointF(p1)
        self._p2 = QPointF(p2)
        self.update()

    # ------------------------------------------------------------------
    # end labels
    # ------------------------------------------------------------------
    def start_label(self) -> str:
        return self._start_label

    def set_start_label(self, text: str) -> None:
        s = str(text)
        if s == self._start_label:
            return
        self.prepareGeometryChange()
        self._start_label = s
        self.update()

    def end_label(self) -> str:
        return self._end_label

    def set_end_label(self, text: str) -> None:
        s = str(text)
        if s == self._end_label:
            return
        self.prepareGeometryChange()
        self._end_label = s
        self.update()

    def start_label_border(self) -> TextBorder:
        return self._start_label_border

    def set_start_label_border(self, border: TextBorder) -> None:
        if border is self._start_label_border:
            return
        self.prepareGeometryChange()
        self._start_label_border = border
        self.update()

    def end_label_border(self) -> TextBorder:
        return self._end_label_border

    def set_end_label_border(self, border: TextBorder) -> None:
        if border is self._end_label_border:
            return
        self.prepareGeometryChange()
        self._end_label_border = border
        self.update()

    def label_border(self) -> TextBorder:
        """Convenience view (start end); kept for callers that treat
        both labels alike."""
        return self._start_label_border

    def set_label_border(self, border: TextBorder) -> None:
        """Convenience: apply the same outline to both labels."""
        self.set_start_label_border(border)
        self.set_end_label_border(border)

    @staticmethod
    def _label_outline_rect(
        rect: QRectF, border: TextBorder
    ) -> QRectF | None:
        """Rect the label's outline is drawn in (None when borderless)."""
        if border is TextBorder.NONE:
            return None
        if border is TextBorder.ELLIPSE:
            return circumscribed_circle_rect(rect)
        return QRectF(rect)

    def _label_font(self) -> QFont:
        font = QFont(_LABEL_FONT_FAMILY, _LABEL_POINT_SIZE)
        font.setStyleHint(QFont.Helvetica)
        return font

    def _label_gap(self, start: bool) -> float:
        """Distance from that endpoint to the near edge of its label.

        Per-end so a datum frame hugs a bare tail while the other end
        still clears its arrowhead; overridden by ArrowItem. A BORDERED
        label touches the line (gap 0 -- the user wants the datum frame
        glued to the leader); only bare text keeps a small breathing
        gap."""
        border = (
            self._start_label_border if start else self._end_label_border
        )
        if border is not TextBorder.NONE:
            return 0.0
        return self._stroke / 2.0 + 3.0

    def _label_rects(self) -> list[tuple[QRectF, str, TextBorder]]:
        """(rect, text, border) for each non-empty end label, positioned
        just past its endpoint along the OUTWARD direction of the end
        segment -- so a bent line pushes the label away from its last
        segment, not along the p1->p2 chord. The label (or its outline,
        when bordered) sits flush against the endpoint: the reach is the
        exact ray/rect boundary distance, not a worst-case diagonal."""
        out: list[tuple[QRectF, str, TextBorder]] = []
        pts = self.path_points()
        fm = QFontMetricsF(self._label_font())
        for text, endpoint, inward, is_start, border in (
            (self._start_label, pts[0], pts[1], True,
             self._start_label_border),
            (self._end_label, pts[-1], pts[-2], False,
             self._end_label_border),
        ):
            if not text:
                continue
            dx = endpoint.x() - inward.x()
            dy = endpoint.y() - inward.y()
            length = math.hypot(dx, dy)
            ux, uy = (dx / length, dy / length) if length > 1e-9 else (1.0, 0.0)
            w = fm.horizontalAdvance(text) + 2 * _LABEL_PADDING
            h = fm.height() + 2 * _LABEL_PADDING
            # Contact is measured against the drawn outline. A circle's
            # boundary is at its radius whatever the direction; for a
            # rect it is the exact ray/edge intersection distance.
            if border is TextBorder.ELLIPSE:
                t = math.hypot(w, h) / 2.0  # circumscribed circle radius
            else:
                tx = (w / 2.0) / abs(ux) if abs(ux) > 1e-9 else float("inf")
                ty = (h / 2.0) / abs(uy) if abs(uy) > 1e-9 else float("inf")
                t = min(tx, ty)
            reach = self._label_gap(is_start) + t
            cx = endpoint.x() + ux * reach
            cy = endpoint.y() + uy * reach
            out.append(
                (QRectF(cx - w / 2.0, cy - h / 2.0, w, h), text, border)
            )
        return out

    def _draw_labels(self, painter) -> None:  # noqa: ANN001
        rects = self._label_rects()
        if not rects:
            return
        painter.save()
        painter.setPen(QPen(self._color))
        painter.setFont(self._label_font())
        for rect, text, _border in rects:
            painter.drawText(rect, Qt.AlignCenter, text)
        border_pen = QPen(self._color, max(1.0, min(self._stroke, 2.0)))
        painter.setBrush(Qt.NoBrush)
        for rect, _text, border in rects:
            outline = self._label_outline_rect(rect, border)
            if outline is None:
                continue
            painter.setPen(border_pen)
            if border is TextBorder.ELLIPSE:
                painter.drawEllipse(outline)
            else:
                painter.drawRect(outline)
        painter.restore()

    # ------------------------------------------------------------------
    # bend points
    # ------------------------------------------------------------------
    def bends(self) -> list[QPointF]:
        return [QPointF(b) for b in self._bends]

    def set_bends(
        self, bends: list[QPointF], radii: list[float] | None = None
    ) -> None:
        """Replace the bends. Without `radii`, the current radii are
        kept when the count is unchanged (bends moved), else dropped."""
        self.prepareGeometryChange()
        self._bends = [QPointF(b) for b in bends]
        if radii is not None:
            self._bend_radii = [max(0.0, float(r)) for r in radii]
        elif len(self._bend_radii) != len(self._bends):
            self._bend_radii = []
        self._fit_radii()
        self.update()

    def _fit_radii(self) -> None:
        n = len(self._bends)
        self._bend_radii = (self._bend_radii + [0.0] * n)[:n]

    def bend_radii(self) -> list[float]:
        """Corner radius of each bend, in item units (0 = sharp)."""
        self._fit_radii()
        return list(self._bend_radii)

    def set_bend_radii(self, radii: list[float]) -> None:
        new = [max(0.0, float(r)) for r in radii]
        if new == self._bend_radii:
            return
        self.prepareGeometryChange()
        self._bend_radii = new
        self._fit_radii()
        self.update()

    def path_points(self) -> list[QPointF]:
        """Full path in drawing order: p1, bends..., p2."""
        return [QPointF(self._p1), *self.bends(), QPointF(self._p2)]

    def insert_bend_near(self, local_pos: QPointF) -> int:
        """Insert a bend on the segment closest to `local_pos`, at its
        projection onto that segment -- or at the segment's middle when
        the projection falls on (or right next to) one of its ends.
        Returns the new bend's index."""
        pts = self.path_points()
        best_seg = 0
        best_d2 = float("inf")
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            abx, aby = b.x() - a.x(), b.y() - a.y()
            ab2 = abx * abx + aby * aby
            if ab2 < 1e-12:
                t = 0.0
            else:
                t = (
                    (local_pos.x() - a.x()) * abx
                    + (local_pos.y() - a.y()) * aby
                ) / ab2
                t = max(0.0, min(1.0, t))
            proj = QPointF(a.x() + t * abx, a.y() + t * aby)
            dx, dy = local_pos.x() - proj.x(), local_pos.y() - proj.y()
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_seg = i
        point = bend_point_on_segment(
            pts[best_seg],
            pts[best_seg + 1],
            local_pos,
            (HANDLE_HIT_HALF + 2.0) * self.screen_px(),
        )
        self.prepareGeometryChange()
        self._fit_radii()
        self._bends.insert(best_seg, point)
        self._bend_radii.insert(best_seg, 0.0)
        self.update()
        return best_seg

    def remove_bend(self, index: int) -> None:
        if 0 <= index < len(self._bends):
            self.prepareGeometryChange()
            self._fit_radii()
            del self._bends[index]
            del self._bend_radii[index]
            self.update()

    def bend_at(
        self, local_pos: QPointF, radius: float | None = None
    ) -> int | None:
        """Index of the bend within `radius` of `local_pos`, or None.
        The default radius is the handle hit area, in screen pixels."""
        if radius is None:
            radius = (HANDLE_HIT_HALF + 1.0) * self.screen_px()
        r2 = radius * radius
        for i, b in enumerate(self._bends):
            dx, dy = local_pos.x() - b.x(), local_pos.y() - b.y()
            if dx * dx + dy * dy <= r2:
                return i
        return None

    def boundingRect(self) -> QRectF:
        m = self._stroke / 2.0 + 4.0 + self.handles_extent()
        pts = self.path_points()
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        rect = QRectF(
            min(xs) - m,
            min(ys) - m,
            max(xs) - min(xs) + 2 * m,
            max(ys) - min(ys) + 2 * m,
        )
        for label_rect, _text, border in self._label_rects():
            outline = self._label_outline_rect(label_rect, border)
            grown = outline if outline is not None else label_rect
            rect = rect.united(grown.adjusted(-2.0, -2.0, 2.0, 2.0))
        return rect

    def _pen(self) -> QPen:
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.RoundCap)
        if self._stroke <= 0.0:
            # QPen(width=0) is a cosmetic hairline in Qt, not "no pen" --
            # an explicit style is needed to actually hide the line.
            pen.setStyle(Qt.NoPen)
            return pen
        return self._apply_dash(pen)

    def _draw_shaft(self, painter) -> None:  # noqa: ANN001
        radii = self.bend_radii()
        if has_rounding(radii):
            # Ends are never rounded: radii sit on the inner points.
            painter.drawPath(
                rounded_path(self.path_points(), [0.0, *radii, 0.0])
            )
            return
        painter.drawPolyline(QPolygonF(self.path_points()))

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        self._draw_shaft(painter)
        self._draw_labels(painter)
        self._draw_selection_marker(painter, self.boundingRect())

    # ------------------------------------------------------------------
    # selection chrome: which end is which (Lot K)
    # ------------------------------------------------------------------
    def _draw_handles(self, painter) -> None:  # noqa: ANN001
        px = self.screen_px()
        half = HANDLE_HALF * px
        painter.save()
        self._draw_direction_chevron(painter, px)
        outline = QPen(HANDLE_COLOR, 0)
        outline.setCosmetic(True)
        painter.setPen(outline)
        painter.setBrush(QColor("#FFFFFF"))
        r_bend = half * 0.8
        for b in self._bends:
            painter.drawEllipse(b, r_bend, r_bend)
        # Filled markers, ringed in white so they read on a dark stroke.
        ring = QPen(QColor("#FFFFFF"), 1.5)
        ring.setCosmetic(True)
        painter.setPen(ring)
        painter.setBrush(HANDLE_COLOR)
        painter.drawEllipse(self._p1, half, half)
        painter.drawRect(
            QRectF(self._p2.x() - half, self._p2.y() - half, 2 * half, 2 * half)
        )
        painter.restore()

    def direction_chevron(self) -> tuple[QPointF, QPointF, QPointF] | None:
        """(barb, tip, barb) of the start-to-end chevron, in local
        coordinates, or None when the path is too short to carry one."""
        px = self.screen_px()
        pts = self.path_points()
        lengths = [
            math.hypot(b.x() - a.x(), b.y() - a.y())
            for a, b in zip(pts, pts[1:])
        ]
        if sum(lengths) < _CHEVRON_MIN_PATH_PX * px:
            return None
        # Middle of the longest segment: always on a straight run, never
        # on a corner where the heading would be ambiguous.
        i = max(range(len(lengths)), key=lambda k: lengths[k])
        a, b, seg = pts[i], pts[i + 1], lengths[i]
        if seg < 1e-9:
            return None
        ux, uy = (b.x() - a.x()) / seg, (b.y() - a.y()) / seg
        mid = QPointF((a.x() + b.x()) / 2.0, (a.y() + b.y()) / 2.0)
        h = _CHEVRON_HALF_PX * px
        tip = QPointF(mid.x() + ux * h, mid.y() + uy * h)
        back = QPointF(mid.x() - ux * h, mid.y() - uy * h)
        nx, ny = -uy, ux
        return (
            QPointF(back.x() + nx * h * 1.2, back.y() + ny * h * 1.2),
            tip,
            QPointF(back.x() - nx * h * 1.2, back.y() - ny * h * 1.2),
        )

    def _draw_direction_chevron(self, painter, px: float) -> None:  # noqa: ANN001
        chevron = self.direction_chevron()
        if chevron is None:
            return
        poly = QPolygonF(list(chevron))
        for color, width in ((QColor("#FFFFFF"), 4.0), (HANDLE_COLOR, 2.0)):
            pen = QPen(color, width)
            pen.setCosmetic(True)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(poly)

    def hoverMoveEvent(self, event) -> None:  # noqa: ANN001
        # Name the end under the cursor, so start and end are never a
        # guess.
        role = self.hit_handle(event.pos()) if self.isSelected() else None
        if role in HANDLE_TIPS:
            tip = HANDLE_TIPS[role]
        elif isinstance(role, int):
            tip = "Bend point"
        else:
            tip = ""
        if self.toolTip() != tip:
            self.setToolTip(tip)
        super().hoverMoveEvent(event)

    # ------------------------------------------------------------------
    # resize handles (two endpoints + one int-keyed handle per bend,
    # mirroring _PolyItem's opaque-role pattern)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict:
        h: dict = {
            HandleRole.P1: QPointF(self._p1),
            HandleRole.P2: QPointF(self._p2),
        }
        for i, b in enumerate(self._bends):
            h[i] = QPointF(b)
        return h

    def apply_resize(self, role, local_pos: QPointF) -> None:  # noqa: ANN001
        if role is HandleRole.P1:
            self.set_line_points(local_pos, self._p2)
        elif role is HandleRole.P2:
            self.set_line_points(self._p1, local_pos)
        elif isinstance(role, int) and 0 <= role < len(self._bends):
            self.prepareGeometryChange()
            self._bends[role] = QPointF(local_pos)
            self.update()

    def geom_snapshot(self) -> object:
        return (
            QPointF(self._p1),
            QPointF(self._p2),
            self.bends(),
            self.bend_radii(),
        )

    def apply_geom(self, snapshot: object) -> None:
        if (
            isinstance(snapshot, tuple)
            and len(snapshot) >= 2
            and isinstance(snapshot[0], QPointF)
        ):
            self.set_line_points(snapshot[0], snapshot[1])
            # Pre-bend snapshots were plain (p1, p2) tuples, pre-radius
            # ones (p1, p2, bends).
            bends = snapshot[2] if len(snapshot) >= 3 else []
            radii = snapshot[3] if len(snapshot) >= 4 else None
            if isinstance(bends, list):
                self.set_bends(bends, radii)

    def scale_geometry(self, s: float) -> None:
        super().scale_geometry(s)
        self.set_line_points(
            QPointF(self._p1.x() * s, self._p1.y() * s),
            QPointF(self._p2.x() * s, self._p2.y() * s),
        )
        self.set_bends(
            [QPointF(b.x() * s, b.y() * s) for b in self._bends],
            [r * s for r in self.bend_radii()],
        )

    def _copy_line_extras_into(self, dst: "LineItem") -> None:
        dst.set_bends(self.bends(), self.bend_radii())
        dst.set_start_label(self._start_label)
        dst.set_end_label(self._end_label)
        dst.set_start_label_border(self._start_label_border)
        dst.set_end_label_border(self._end_label_border)

    def clone(self) -> "LineItem":
        c = LineItem(QPointF(self._p1), QPointF(self._p2))
        self._copy_base_style_into(c)
        self._copy_line_extras_into(c)
        return c


class ArrowItem(LineItem):
    KIND = "arrow"

    HEAD_LEN_FACTOR = 5.0  # head size = stroke * factor
    HEAD_HALF_ANGLE = HEAD_HALF_ANGLE

    def __init__(
        self,
        p1: QPointF,
        p2: QPointF,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(p1, p2, parent)
        self._start_end: EndStyle = EndStyle.NONE
        self._end_end: EndStyle = EndStyle.OPEN_ARROW

    # ------------------------------------------------------------------
    # ends
    # ------------------------------------------------------------------
    def start_end(self) -> EndStyle:
        return self._start_end

    def set_start_end(self, style: EndStyle) -> None:
        if style is self._start_end:
            return
        self.prepareGeometryChange()
        self._start_end = style
        self.update()

    def end_end(self) -> EndStyle:
        return self._end_end

    def set_end_end(self, style: EndStyle) -> None:
        if style is self._end_end:
            return
        self.prepareGeometryChange()
        self._end_end = style
        self.update()

    def boundingRect(self) -> QRectF:
        base = super().boundingRect()
        head = max(self._stroke * self.HEAD_LEN_FACTOR, 8.0)
        return base.adjusted(-head, -head, head, head)

    # ------------------------------------------------------------------
    # head geometry
    # ------------------------------------------------------------------
    def _head_size(self) -> float:
        return max(self._stroke * self.HEAD_LEN_FACTOR, 8.0)

    def _draw_end(
        self, painter, anchor: QPointF, towards: QPointF, style: EndStyle
    ) -> None:
        """Draw `style` at `anchor`, oriented along anchor->towards."""
        draw_line_end(
            painter, anchor, towards, style, self._head_size(), self._color
        )

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        self._draw_shaft(painter)
        # Heads use a solid pen (dash patterns shouldn't ghost the
        # decoration) and the item color. On a bent line each head is
        # oriented along its own *end segment*, not the p1->p2 chord.
        head_pen = QPen(self._color, self._stroke)
        head_pen.setCapStyle(Qt.RoundCap)
        head_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(head_pen)
        pts = self.path_points()
        self._draw_end(painter, pts[0], pts[1], self._start_end)
        self._draw_end(painter, pts[-1], pts[-2], self._end_end)
        self._draw_labels(painter)
        self._draw_selection_marker(painter, self.boundingRect())

    def _label_gap(self, start: bool) -> float:
        # Clear the arrowhead when THAT end has one; a bare end keeps
        # the label flush against the line like a plain LineItem (the
        # datum-frame case: triangle at one end, boxed letter hugging
        # the other).
        style = self._start_end if start else self._end_end
        if style is EndStyle.NONE:
            return super()._label_gap(start)
        return max(self._head_size(), self._stroke / 2.0 + 3.0) + 2.0

    def clone(self) -> "ArrowItem":
        c = ArrowItem(QPointF(self._p1), QPointF(self._p2))
        self._copy_base_style_into(c)
        self._copy_line_extras_into(c)
        c.set_start_end(self._start_end)
        c.set_end_end(self._end_end)
        return c
