"""CalloutItem: a free-text box with a leader line ending in an arrow.

Extends `TextAnnotationItem` (so it inherits inline editing, fonts,
alignment and the wrap-resize handles) and adds a single leader: a line
from the nearest point of the text box to a draggable tip, with an open
arrowhead at the tip. The tip is stored in item-local coordinates, like
the text box (which sits at local origin); a dedicated handle (reusing
`HandleRole.P1`) repositions it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from annoter.model.styles import HandleRole
from annoter.views.items.text import TextAnnotationItem


_DEFAULT_TIP = QPointF(-40.0, 48.0)
_HEAD_LEN_FACTOR = 5.0
_HEAD_HALF_ANGLE = math.radians(22.0)


class CalloutItem(TextAnnotationItem):
    KIND = "callout"

    def __init__(
        self,
        pos: QPointF,
        text: str = "",
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(pos, text, parent)
        self._tip: QPointF = QPointF(_DEFAULT_TIP)

    # ------------------------------------------------------------------
    # leader geometry
    # ------------------------------------------------------------------
    def tip(self) -> QPointF:
        return QPointF(self._tip)

    def set_tip(self, p: QPointF) -> None:
        self.prepareGeometryChange()
        self._tip = QPointF(p)
        self.update()

    def connection_point(self) -> QPointF:
        """Point on the text box border closest to the tip (local coords)."""
        r = self.content_rect()
        if r.contains(self._tip):
            return r.center()
        cx = min(max(self._tip.x(), r.left()), r.right())
        cy = min(max(self._tip.y(), r.top()), r.bottom())
        return QPointF(cx, cy)

    def _head_size(self) -> float:
        return max(self._stroke * _HEAD_LEN_FACTOR, 10.0)

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------
    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        conn = self.connection_point()
        tip = self._tip
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(conn, tip)
        self._draw_arrow_head(painter, tip, conn)
        # The inner QGraphicsTextItem paints the text; super draws the
        # selection marker (and the handles, including the tip handle).
        super().paint(painter, option, widget)

    def _draw_arrow_head(
        self, painter, tip: QPointF, towards: QPointF
    ) -> None:
        dx = tip.x() - towards.x()
        dy = tip.y() - towards.y()
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return
        ang = math.atan2(dy, dx)
        size = self._head_size()
        a1 = ang - _HEAD_HALF_ANGLE + math.pi
        a2 = ang + _HEAD_HALF_ANGLE + math.pi
        h1 = QPointF(
            tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1)
        )
        h2 = QPointF(
            tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2)
        )
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF([h1, tip, h2]))

    # ------------------------------------------------------------------
    # geometry bounds (text box + leader + arrowhead)
    # ------------------------------------------------------------------
    def boundingRect(self) -> QRectF:
        base = super().boundingRect()
        head = self._head_size()
        leader = QRectF(self._tip, self.connection_point()).normalized()
        leader = leader.adjusted(-head, -head, head, head)
        return base.united(leader)

    # ------------------------------------------------------------------
    # handles: text-box handles plus the leader tip (HandleRole.P1)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict:
        h = dict(super().handle_positions())
        h[HandleRole.P1] = QPointF(self._tip)
        return h

    def apply_resize(self, role, local_pos: QPointF) -> None:  # noqa: ANN001
        if role is HandleRole.P1:
            self.set_tip(QPointF(local_pos))
        else:
            super().apply_resize(role, local_pos)

    def geom_snapshot(self) -> object:
        return (super().geom_snapshot(), QPointF(self._tip))

    def apply_geom(self, snapshot: object) -> None:
        if isinstance(snapshot, tuple) and len(snapshot) == 2:
            super().apply_geom(snapshot[0])
            self.set_tip(snapshot[1])

    def clone(self) -> "CalloutItem":
        c = CalloutItem(QPointF(self.pos()), self.text())
        c.set_color(self.color())
        c.set_stroke(self.stroke())
        c.set_dash_style(self.dash_style())
        c.set_font_family(self.font_family())
        c.set_font_size(self.font_size())
        c.set_bold(self.bold())
        c.set_italic(self.italic())
        c.set_align(self.align())
        c.set_tip(self.tip())
        return c
