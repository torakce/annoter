"""Polyline and Polygon annotation items (multi-vertex).

Both store an ordered list of vertices. `PolylineItem` renders an open
path; `PolygonItem` closes the path and can be filled. Vertices are
placed by successive clicks (see `PdfScene`); each vertex gets its own
resize handle, keyed by integer index -- the base item treats the
handle "role" opaquely, so an int works as well as a `HandleRole`.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from annoter.views.items.base import AnnotationItem


class _PolyItem(AnnotationItem):
    """Shared vertex storage, geometry and per-vertex handles."""

    CLOSED: bool = False

    def __init__(
        self,
        points: list[QPointF] | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._points: list[QPointF] = (
            [QPointF(p) for p in points] if points else []
        )

    # ------------------------------------------------------------------
    # vertices
    # ------------------------------------------------------------------
    def points(self) -> list[QPointF]:
        return [QPointF(p) for p in self._points]

    def set_points(self, points: list[QPointF]) -> None:
        self.prepareGeometryChange()
        self._points = [QPointF(p) for p in points]
        self.update()

    def _path(self) -> QPainterPath:
        path = QPainterPath()
        if not self._points:
            return path
        path.moveTo(self._points[0])
        for p in self._points[1:]:
            path.lineTo(p)
        if self.CLOSED and len(self._points) >= 3:
            path.closeSubpath()
        return path

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        m = self._stroke / 2.0 + 1.0 + self.handles_extent()
        xs = [p.x() for p in self._points]
        ys = [p.y() for p in self._points]
        return QRectF(
            min(xs) - m,
            min(ys) - m,
            max(xs) - min(xs) + 2 * m,
            max(ys) - min(ys) + 2 * m,
        )

    def _pen(self) -> QPen:
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return self._apply_dash(pen)

    def _brush(self) -> QBrush:
        return QBrush(Qt.NoBrush)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        if not self._points:
            return
        painter.setPen(self._pen())
        painter.setBrush(self._brush())
        painter.drawPath(self._path())
        self._draw_selection_marker(painter, self.boundingRect())

    # ------------------------------------------------------------------
    # per-vertex resize handles (keyed by vertex index)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict:
        return {i: QPointF(p) for i, p in enumerate(self._points)}

    def apply_resize(self, role, local_pos: QPointF) -> None:  # noqa: ANN001
        if isinstance(role, int) and 0 <= role < len(self._points):
            self.prepareGeometryChange()
            self._points[role] = QPointF(local_pos)
            self.update()

    def geom_snapshot(self) -> object:
        return [QPointF(p) for p in self._points]

    def apply_geom(self, snapshot: object) -> None:
        if isinstance(snapshot, list):
            self.set_points(snapshot)


class PolylineItem(_PolyItem):
    """Open multi-segment path."""

    KIND = "polyline"
    CLOSED = False

    def clone(self) -> "PolylineItem":
        c = PolylineItem([QPointF(p) for p in self._points])
        self._copy_base_style_into(c)
        return c


class PolygonItem(_PolyItem):
    """Closed multi-segment shape with an optional fill."""

    KIND = "polygon"
    CLOSED = True

    def __init__(
        self,
        points: list[QPointF] | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(points, parent)
        self._fill_enabled: bool = False
        self._fill_color: QColor = QColor("#FFEB3B")

    def fill_enabled(self) -> bool:
        return self._fill_enabled

    def set_fill_enabled(self, enabled: bool) -> None:
        if bool(enabled) == self._fill_enabled:
            return
        self._fill_enabled = bool(enabled)
        self.update()

    def fill_color(self) -> QColor:
        return QColor(self._fill_color)

    def set_fill_color(self, color: QColor) -> None:
        c = QColor(color)
        if c == self._fill_color:
            return
        self._fill_color = c
        self.update()

    def _brush(self) -> QBrush:
        if self._fill_enabled:
            return QBrush(self._fill_color)
        return QBrush(Qt.NoBrush)

    def clone(self) -> "PolygonItem":
        c = PolygonItem([QPointF(p) for p in self._points])
        self._copy_base_style_into(c)
        c.set_fill_enabled(self._fill_enabled)
        c.set_fill_color(self._fill_color)
        return c
