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

from annoter.views.items.base import HANDLE_HIT_HALF, AnnotationItem
from annoter.views.items.rounding import has_rounding, rounded_path


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
        # One corner radius per vertex (0 = sharp), 2026-09-28. The two
        # ends of an open polyline are never rounded.
        self._radii: list[float] = []

    # ------------------------------------------------------------------
    # vertices
    # ------------------------------------------------------------------
    def points(self) -> list[QPointF]:
        return [QPointF(p) for p in self._points]

    def set_points(
        self, points: list[QPointF], radii: list[float] | None = None
    ) -> None:
        """Replace the vertices. Without `radii`, the current radii are
        kept when the count is unchanged (vertices moved), else dropped."""
        self.prepareGeometryChange()
        self._points = [QPointF(p) for p in points]
        if radii is not None:
            self._radii = [max(0.0, float(r)) for r in radii]
        elif len(self._radii) != len(self._points):
            self._radii = []
        n = len(self._points)
        self._radii = (self._radii + [0.0] * n)[:n]
        self.update()

    def bend_radii(self) -> list[float]:
        """Corner radius of each vertex, in item units (0 = sharp)."""
        n = len(self._points)
        return (list(self._radii) + [0.0] * n)[:n]

    def set_bend_radii(self, radii: list[float]) -> None:
        n = len(self._points)
        new = ([max(0.0, float(r)) for r in radii] + [0.0] * n)[:n]
        if new == self.bend_radii():
            return
        self.prepareGeometryChange()
        self._radii = new
        self.update()

    def corner_indices(self) -> list[int]:
        """Vertices that can be rounded (not the ends of an open path)."""
        n = len(self._points)
        return list(range(n)) if self.CLOSED else list(range(1, n - 1))

    def vertex_at(
        self, local_pos: QPointF, radius: float | None = None
    ) -> int | None:
        """Index of the vertex within `radius` of `local_pos` (default:
        the handle hit area, in screen pixels), or None."""
        if radius is None:
            radius = (HANDLE_HIT_HALF + 1.0) * self.screen_px()
        r2 = radius * radius
        for i, p in enumerate(self._points):
            dx, dy = local_pos.x() - p.x(), local_pos.y() - p.y()
            if dx * dx + dy * dy <= r2:
                return i
        return None

    def _path(self) -> QPainterPath:
        path = QPainterPath()
        if not self._points:
            return path
        radii = self.bend_radii()
        if has_rounding(radii) and len(self._points) >= 3:
            return rounded_path(
                self._points, radii, closed=self.CLOSED
            )
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
        if self._stroke <= 0.0:
            # QPen(width=0) is a cosmetic hairline in Qt, not "no pen" --
            # an explicit style is needed to show only the fill.
            pen.setStyle(Qt.NoPen)
            return pen
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
        return ([QPointF(p) for p in self._points], self.bend_radii())

    def apply_geom(self, snapshot: object) -> None:
        if isinstance(snapshot, list):  # before corner radii
            self.set_points(snapshot)
        elif isinstance(snapshot, tuple) and len(snapshot) == 2:
            self.set_points(snapshot[0], snapshot[1])

    def scale_geometry(self, s: float) -> None:
        super().scale_geometry(s)
        self.set_points(
            [QPointF(p.x() * s, p.y() * s) for p in self._points],
            [r * s for r in self.bend_radii()],
        )


class PolylineItem(_PolyItem):
    """Open multi-segment path."""

    KIND = "polyline"
    CLOSED = False

    def clone(self) -> "PolylineItem":
        c = PolylineItem([QPointF(p) for p in self._points])
        c.set_bend_radii(self.bend_radii())
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
        self._fill_opacity: float = 1.0

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

    def fill_opacity(self) -> float:
        return self._fill_opacity

    def set_fill_opacity(self, opacity: float) -> None:
        v = max(0.0, min(1.0, float(opacity)))
        if v == self._fill_opacity:
            return
        self._fill_opacity = v
        self.update()

    def _brush(self) -> QBrush:
        if self._fill_enabled:
            c = QColor(self._fill_color)
            c.setAlphaF(self._fill_opacity)
            return QBrush(c)
        return QBrush(Qt.NoBrush)

    def clone(self) -> "PolygonItem":
        c = PolygonItem([QPointF(p) for p in self._points])
        c.set_bend_radii(self.bend_radii())
        self._copy_base_style_into(c)
        c.set_fill_enabled(self._fill_enabled)
        c.set_fill_color(self._fill_color)
        c.set_fill_opacity(self._fill_opacity)
        return c
