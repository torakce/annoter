"""FreehandItem: freehand stroke stored as a polyline.

Selected, it is highlighted -- a translucent band in the selection color
along the stroke itself -- rather than framed in a dashed box: a scribble
is a line, and a box around it mostly covers what is underneath (user
feedback, 2026-09-28). It has no handles, so nothing else is drawn.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem

from annoter.views.items.base import HANDLE_COLOR, AnnotationItem

# Selection highlight: this many screen pixels on each side of the stroke.
HIGHLIGHT_PX = 4.0
HIGHLIGHT_ALPHA = 90


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

    def highlight_width(self) -> float:
        """Width of the selection band, in item units."""
        return self._stroke + 2 * HIGHLIGHT_PX * self.screen_px()

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        m = self._stroke / 2.0 + 1.0
        if self.isSelected():
            m = max(m, self.highlight_width() / 2.0 + 1.0)
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
        if self._stroke <= 0.0:
            # QPen(width=0) is a cosmetic hairline in Qt, not "no pen".
            pen.setStyle(Qt.NoPen)
        painter.setBrush(Qt.NoBrush)
        if self.isSelected():
            band_color = QColor(HANDLE_COLOR)
            band_color.setAlpha(HIGHLIGHT_ALPHA)
            band = QPen(band_color, self.highlight_width())
            band.setCapStyle(Qt.RoundCap)
            band.setJoinStyle(Qt.RoundJoin)
            painter.setPen(band)
            painter.drawPath(self._path())
        painter.setPen(pen)
        painter.drawPath(self._path())

    def scale_geometry(self, s: float) -> None:
        super().scale_geometry(s)
        self.set_points(
            [QPointF(p.x() * s, p.y() * s) for p in self._points]
        )

    def clone(self) -> "FreehandItem":
        c = FreehandItem([QPointF(p) for p in self._points])
        self._copy_base_style_into(c)
        return c
