"""FreehandItem: freehand stroke stored as a polyline."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from annoter.views.items.base import AnnotationItem


class FreehandItem(AnnotationItem):
    KIND = "ink"

    def __init__(
        self,
        points: list[QPointF] | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._points: list[QPointF] = (
            [QPointF(p) for p in points] if points else []
        )

    def points(self) -> list[QPointF]:
        return [QPointF(p) for p in self._points]

    def set_points(self, points: list[QPointF]) -> None:
        self.prepareGeometryChange()
        self._points = [QPointF(p) for p in points]
        self.update()

    def add_point(self, p: QPointF) -> None:
        self.prepareGeometryChange()
        self._points.append(QPointF(p))
        self.update()

    def _path(self) -> QPainterPath:
        path = QPainterPath()
        if not self._points:
            return path
        path.moveTo(self._points[0])
        for p in self._points[1:]:
            path.lineTo(p)
        return path

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        m = self._stroke / 2.0 + 1.0
        xs = [p.x() for p in self._points]
        ys = [p.y() for p in self._points]
        return QRectF(
            min(xs) - m,
            min(ys) - m,
            max(xs) - min(xs) + 2 * m,
            max(ys) - min(ys) + 2 * m,
        )

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        if not self._points:
            return
        pen = QPen(self._color, self._stroke)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._path())
        self._draw_selection_marker(painter, self.boundingRect())

    def clone(self) -> "FreehandItem":
        c = FreehandItem([QPointF(p) for p in self._points])
        self._copy_base_style_into(c)
        return c
