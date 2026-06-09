"""Line and Arrow annotation items.

Arrow line-endings are configurable on both ends via the EndStyle enum;
this lets the user draw arrows in either direction or two-headed arrows.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from annoter.model.styles import EndStyle, HandleRole
from annoter.views.items.base import AnnotationItem


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

    def line_points(self) -> tuple[QPointF, QPointF]:
        return QPointF(self._p1), QPointF(self._p2)

    def set_line_points(self, p1: QPointF, p2: QPointF) -> None:
        self.prepareGeometryChange()
        self._p1 = QPointF(p1)
        self._p2 = QPointF(p2)
        self.update()

    def boundingRect(self) -> QRectF:
        m = self._stroke / 2.0 + 4.0 + self.handles_extent()
        x1, y1 = self._p1.x(), self._p1.y()
        x2, y2 = self._p2.x(), self._p2.y()
        return QRectF(
            min(x1, x2) - m,
            min(y1, y2) - m,
            abs(x2 - x1) + 2 * m,
            abs(y2 - y1) + 2 * m,
        )

    def _pen(self) -> QPen:
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.RoundCap)
        return self._apply_dash(pen)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(self._p1, self._p2)
        self._draw_selection_marker(painter, self.boundingRect())

    # ------------------------------------------------------------------
    # resize handles (two endpoints)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict[HandleRole, QPointF]:
        return {
            HandleRole.P1: QPointF(self._p1),
            HandleRole.P2: QPointF(self._p2),
        }

    def apply_resize(self, role: HandleRole, local_pos: QPointF) -> None:
        if role is HandleRole.P1:
            self.set_line_points(local_pos, self._p2)
        elif role is HandleRole.P2:
            self.set_line_points(self._p1, local_pos)

    def geom_snapshot(self) -> object:
        return (QPointF(self._p1), QPointF(self._p2))

    def apply_geom(self, snapshot: object) -> None:
        if (
            isinstance(snapshot, tuple)
            and len(snapshot) == 2
            and isinstance(snapshot[0], QPointF)
        ):
            self.set_line_points(snapshot[0], snapshot[1])

    def clone(self) -> "LineItem":
        c = LineItem(QPointF(self._p1), QPointF(self._p2))
        self._copy_base_style_into(c)
        return c


class ArrowItem(LineItem):
    KIND = "arrow"

    HEAD_LEN_FACTOR = 5.0  # head size = stroke * factor
    HEAD_HALF_ANGLE = math.radians(22.0)

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
        if style is EndStyle.NONE:
            return
        dx = towards.x() - anchor.x()
        dy = towards.y() - anchor.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ang = math.atan2(dy, dx)
        size = self._head_size()

        if style in (EndStyle.OPEN_ARROW, EndStyle.CLOSED_ARROW):
            a1 = ang - self.HEAD_HALF_ANGLE
            a2 = ang + self.HEAD_HALF_ANGLE
            h1 = QPointF(
                anchor.x() + size * math.cos(a1),
                anchor.y() + size * math.sin(a1),
            )
            h2 = QPointF(
                anchor.x() + size * math.cos(a2),
                anchor.y() + size * math.sin(a2),
            )
            poly = QPolygonF([anchor, h1, h2])
            if style is EndStyle.CLOSED_ARROW:
                painter.setBrush(self._color)
            else:
                painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(poly)
            return

        if style is EndStyle.BUTT:
            # Perpendicular tick at the anchor.
            half = size * 0.4
            perp = ang + math.pi / 2
            p1 = QPointF(
                anchor.x() + half * math.cos(perp),
                anchor.y() + half * math.sin(perp),
            )
            p2 = QPointF(
                anchor.x() - half * math.cos(perp),
                anchor.y() - half * math.sin(perp),
            )
            painter.drawLine(p1, p2)
            return

        if style is EndStyle.SLASH:
            half = size * 0.5
            slash = ang + math.radians(60.0)
            p1 = QPointF(
                anchor.x() + half * math.cos(slash),
                anchor.y() + half * math.sin(slash),
            )
            p2 = QPointF(
                anchor.x() - half * math.cos(slash),
                anchor.y() - half * math.sin(slash),
            )
            painter.drawLine(p1, p2)
            return

        if style is EndStyle.DIAMOND:
            half = size * 0.4
            tip1 = QPointF(
                anchor.x() + half * math.cos(ang),
                anchor.y() + half * math.sin(ang),
            )
            tip2 = QPointF(
                anchor.x() - half * math.cos(ang),
                anchor.y() - half * math.sin(ang),
            )
            perp = ang + math.pi / 2
            tip3 = QPointF(
                anchor.x() + half * math.cos(perp),
                anchor.y() + half * math.sin(perp),
            )
            tip4 = QPointF(
                anchor.x() - half * math.cos(perp),
                anchor.y() - half * math.sin(perp),
            )
            painter.setBrush(self._color)
            painter.drawPolygon(QPolygonF([tip1, tip3, tip2, tip4]))
            return

        if style is EndStyle.CIRCLE:
            r = size * 0.35
            painter.setBrush(self._color)
            painter.drawEllipse(anchor, r, r)
            return

        if style is EndStyle.SQUARE:
            half = size * 0.3
            painter.setBrush(self._color)
            painter.drawRect(
                QRectF(
                    anchor.x() - half,
                    anchor.y() - half,
                    2 * half,
                    2 * half,
                )
            )
            return

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(self._p1, self._p2)
        # Heads use a solid pen (dash patterns shouldn't ghost the
        # decoration) and the item color.
        head_pen = QPen(self._color, self._stroke)
        head_pen.setCapStyle(Qt.RoundCap)
        head_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(head_pen)
        self._draw_end(painter, self._p1, self._p2, self._start_end)
        self._draw_end(painter, self._p2, self._p1, self._end_end)
        self._draw_selection_marker(painter, self.boundingRect())

    def clone(self) -> "ArrowItem":
        c = ArrowItem(QPointF(self._p1), QPointF(self._p2))
        self._copy_base_style_into(c)
        c.set_start_end(self._start_end)
        c.set_end_end(self._end_end)
        return c
