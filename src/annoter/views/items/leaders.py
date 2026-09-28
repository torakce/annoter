"""Leaders on texts and GD&T frames: CATIA's "Add Leader" (Lot M).

A leader is a thin line from the annotation's frame to the feature it
points at, ending in an extremity shape (a filled arrowhead by default;
the same shapes as the ends of a line). An annotation can carry any
number of them, each with optional bend points.

Where things live:

- The target and the bends are stored in the annotation's PARENT
  coordinates (the page), not its own. Moving the annotation therefore
  leaves every leader pointing at the same spot on the drawing -- only
  the start, attached to the frame, follows -- which is how CATIA
  behaves: the leader belongs to the geometry it designates.
- The start is not stored: it is the middle of the frame's left or
  right side facing the leader's first point (first bend, else the
  target), or of its top / bottom side when that point lies straight
  above or below -- so it switches sides on its own when the annotation
  moves past its target.

`LeaderHost` is mixed into TextAnnotationItem and GdtAnnotationItem
(before AnnotationItem in the bases). The host provides
`leader_frame_rect()` (local rect the leaders attach to) and folds the
leader pieces into its own bounding rect, shape, handles and geometry
snapshot. The list itself is swapped as a whole by `set_leaders`, which
is what ChangePropsCommand("leaders") calls, so adding, removing or
restyling a leader is one undo step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from annoter.model.styles import EndStyle
from annoter.views.items.line_ends import bend_point_on_segment, draw_line_end
from annoter.views.items.rounding import (
    has_rounding,
    rounded_path,
    rounded_points,
)

Point = tuple[float, float]

DEFAULT_LEADER_END = EndStyle.CLOSED_ARROW
_HIT_PX = 6.0  # screen pixels around a leader that still count as on it

# Handle roles (opaque to the scene, like a bent line's int roles).
TARGET = "leader"
BEND = "leader-bend"


@dataclass(frozen=True)
class Leader:
    """One leader: its target and bends in parent (page) coordinates."""

    target: Point
    bends: tuple[Point, ...] = ()
    end: EndStyle = DEFAULT_LEADER_END
    # Corner radius of each bend (0 = sharp); may be shorter than
    # `bends` (missing = 0). 2026-09-28.
    radii: tuple[float, ...] = ()

    def points(self) -> list[Point]:
        """Bends then target, in drawing order after the start."""
        return [*self.bends, self.target]

    def bend_radii(self) -> list[float]:
        n = len(self.bends)
        return (list(self.radii) + [0.0] * n)[:n]

    def with_bend_radius(self, index: int, radius: float) -> "Leader":
        radii = self.bend_radii()
        if 0 <= index < len(radii):
            radii[index] = max(0.0, float(radius))
        return replace(self, radii=tuple(radii))

    def without_bend(self, index: int) -> "Leader":
        bends = list(self.bends)
        radii = self.bend_radii()
        if 0 <= index < len(bends):
            del bends[index]
            del radii[index]
        return replace(self, bends=tuple(bends), radii=tuple(radii))

    def translated(self, dx: float, dy: float) -> "Leader":
        return replace(
            self,
            target=(self.target[0] + dx, self.target[1] + dy),
            bends=tuple((x + dx, y + dy) for x, y in self.bends),
        )

    def scaled(self, s: float) -> "Leader":
        return replace(
            self,
            target=(self.target[0] * s, self.target[1] * s),
            bends=tuple((x * s, y * s) for x, y in self.bends),
            radii=tuple(r * s for r in self.radii),
        )

    def to_dict(self, to_unit=lambda v: v) -> dict:  # noqa: ANN001
        """JSON form; `to_unit` converts each coordinate (px -> pt)."""
        data: dict = {
            "target": [to_unit(self.target[0]), to_unit(self.target[1])],
            "end": self.end.value,
        }
        if self.bends:
            data["bends"] = [[to_unit(x), to_unit(y)] for x, y in self.bends]
        if any(r > 0.0 for r in self.radii):
            data["radii"] = [to_unit(r) for r in self.bend_radii()]
        return data

    @classmethod
    def from_dict(cls, data: dict, from_unit=lambda v: v) -> "Leader":  # noqa: ANN001
        tx, ty = (float(v) for v in data["target"])
        bends = tuple(
            (from_unit(float(x)), from_unit(float(y)))
            for x, y in data.get("bends", [])
        )
        try:
            end = EndStyle(data.get("end", DEFAULT_LEADER_END.value))
        except ValueError:
            end = DEFAULT_LEADER_END
        radii = tuple(
            max(0.0, from_unit(float(r))) for r in data.get("radii", [])
        )
        return cls((from_unit(tx), from_unit(ty)), bends, end, radii)


def anchor_on_rect(rect: QRectF, toward: QPointF) -> QPointF:
    """Where a leader heading for `toward` leaves `rect`: the middle of
    the left or right side when the point lies beyond that side (the
    usual drawing convention, CATIA's too), the middle of the top or
    bottom side when it lies straight above or below the frame."""
    c = rect.center()
    if toward.x() < rect.left():
        return QPointF(rect.left(), c.y())
    if toward.x() > rect.right():
        return QPointF(rect.right(), c.y())
    y = rect.top() if toward.y() < c.y() else rect.bottom()
    return QPointF(c.x(), y)


def _dist_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
    abx, aby = b.x() - a.x(), b.y() - a.y()
    ab2 = abx * abx + aby * aby
    if ab2 < 1e-12:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = ((p.x() - a.x()) * abx + (p.y() - a.y()) * aby) / ab2
    t = max(0.0, min(1.0, t))
    return math.hypot(p.x() - (a.x() + t * abx), p.y() - (a.y() + t * aby))


class LeaderHost:
    """Leader support for an annotation item (see the module docstring).

    Plain mixin (no __init__): state is created on first use so it works
    whatever the host's constructor does."""

    # ------------------------------------------------------------------
    # state
    # ------------------------------------------------------------------
    def leaders(self) -> list[Leader]:
        return list(getattr(self, "_leaders", ()))

    def set_leaders(self, leaders) -> None:  # noqa: ANN001
        new = [ld for ld in leaders if isinstance(ld, Leader)]
        if new == self.leaders():
            return
        self.prepareGeometryChange()
        self._leaders = new
        self.update()

    def has_leaders(self) -> bool:
        return bool(getattr(self, "_leaders", ()))

    def set_leader_preview(self, target: QPointF | None) -> None:
        """Rubber-band leader to `target` (parent coordinates) while the
        user picks where a new leader points; None removes it."""
        self.prepareGeometryChange()
        self._leader_preview = None if target is None else QPointF(target)
        self.update()

    def leader_preview(self) -> QPointF | None:
        return getattr(self, "_leader_preview", None)

    def set_leaders_hidden(self, hidden: bool) -> None:
        """Leave the leaders out of `paint` (PDF appearance rasterizing:
        they are written as separate line annotations)."""
        self._leaders_hidden = bool(hidden)

    # ------------------------------------------------------------------
    # geometry (local coordinates)
    # ------------------------------------------------------------------
    def leader_frame_rect(self) -> QRectF:  # pragma: no cover - host hook
        raise NotImplementedError

    def _to_local(self, pt: Point | QPointF) -> QPointF:
        q = pt if isinstance(pt, QPointF) else QPointF(pt[0], pt[1])
        return self.mapFromParent(q)

    def leader_path(self, index: int) -> list[QPointF]:
        """Start, bends, target of leader `index`, in local coordinates."""
        return self._path_for(self.leaders()[index])

    def _path_for(self, leader: Leader) -> list[QPointF]:
        rest = [self._to_local(p) for p in leader.points()]
        start = anchor_on_rect(self.leader_frame_rect(), rest[0])
        return [start, *rest]

    def _all_paths(
        self,
    ) -> list[tuple[list[QPointF], EndStyle, bool, list[float]]]:
        """(path, end style, is_preview, bend radii) for everything to
        draw."""
        out = [
            (self._path_for(ld), ld.end, False, ld.bend_radii())
            for ld in self.leaders()
        ]
        preview = self.leader_preview()
        if preview is not None:
            out.append(
                (self._path_for(Leader((preview.x(), preview.y()))),
                 DEFAULT_LEADER_END, True, [])
            )
        return out

    def leader_drawn_points(self, index: int) -> list[QPointF]:
        """Leader `index` as drawn (rounded bends sampled), local
        coordinates: what external viewers get."""
        ld = self.leaders()[index]
        return rounded_points(
            self._path_for(ld), [0.0, *ld.bend_radii(), 0.0]
        )

    def leader_stroke(self) -> float:
        return max(1.0, float(self._stroke))

    def leader_head_size(self) -> float:
        return max(self.leader_stroke() * 5.0, 8.0)

    def leader_bounds(self) -> QRectF:
        """Local rect covering every leader (empty without leaders)."""
        rect = QRectF()
        m = self.leader_head_size() + self.leader_stroke()
        if self.isSelected():
            m += self.handles_extent()
        for path, _end, _preview, _radii in self._all_paths():
            xs = [p.x() for p in path]
            ys = [p.y() for p in path]
            r = QRectF(
                min(xs) - m, min(ys) - m,
                max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m,
            )
            rect = r if rect.isNull() else rect.united(r)
        return rect

    def with_leader_bounds(self, frame: QRectF) -> QRectF:
        extra = self.leader_bounds()
        return frame if extra.isNull() else frame.united(extra)

    def leader_shape(self) -> QPainterPath:
        """Band along every leader, a few screen pixels wide: what a
        click on a leader hits."""
        band = QPainterPath()
        width = max(2 * _HIT_PX * self.screen_px(), self.leader_stroke())
        stroker = QPainterPathStroker()
        stroker.setWidth(width)
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        for path, _end, _preview, _radii in self._all_paths():
            line = QPainterPath()
            line.addPolygon(QPolygonF(path))
            band = band.united(stroker.createStroke(line))
        return band

    def shape_with_leaders(self, frame: QRectF) -> QPainterPath:
        path = QPainterPath()
        path.addRect(frame)
        if self.has_leaders() or self.leader_preview() is not None:
            path = path.united(self.leader_shape())
        return path

    # ------------------------------------------------------------------
    # paint
    # ------------------------------------------------------------------
    def paint_leaders(self, painter) -> None:  # noqa: ANN001
        if getattr(self, "_leaders_hidden", False):
            return
        paths = self._all_paths()
        if not paths:
            return
        painter.save()
        size = self.leader_head_size()
        for path, end, preview, ld_radii in paths:
            pen = QPen(self._color, self.leader_stroke())
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if preview:
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            radii = [0.0, *ld_radii, 0.0]
            if has_rounding(radii):
                painter.drawPath(rounded_path(path, radii))
            else:
                painter.drawPolyline(QPolygonF(path))
            solid = QPen(pen)
            solid.setStyle(Qt.SolidLine)
            painter.setPen(solid)
            draw_line_end(painter, path[-1], path[-2], end, size, self._color)
        painter.restore()

    # ------------------------------------------------------------------
    # handles: each target and each bend is draggable
    # ------------------------------------------------------------------
    def leader_handle_positions(self) -> dict:
        out: dict = {}
        for i, ld in enumerate(self.leaders()):
            for j, b in enumerate(ld.bends):
                out[(BEND, i, j)] = self._to_local(b)
            out[(TARGET, i)] = self._to_local(ld.target)
        return out

    @staticmethod
    def is_leader_role(role) -> bool:  # noqa: ANN001
        return isinstance(role, tuple) and bool(role) and role[0] in (
            TARGET,
            BEND,
        )

    def apply_leader_handle(self, role, local_pos: QPointF) -> None:  # noqa: ANN001
        p = self.mapToParent(local_pos)
        pt = (p.x(), p.y())
        leaders = self.leaders()
        if role[0] == TARGET and 0 <= role[1] < len(leaders):
            leaders[role[1]] = replace(leaders[role[1]], target=pt)
        elif role[0] == BEND and 0 <= role[1] < len(leaders):
            ld = leaders[role[1]]
            if 0 <= role[2] < len(ld.bends):
                bends = list(ld.bends)
                bends[role[2]] = pt
                leaders[role[1]] = replace(ld, bends=tuple(bends))
        self.set_leaders(leaders)

    # ------------------------------------------------------------------
    # hit tests (context menu)
    # ------------------------------------------------------------------
    def leader_at(self, local_pos: QPointF) -> int | None:
        """Index of the leader passing within a few screen pixels of
        `local_pos` (topmost = last added first), or None."""
        tol = _HIT_PX * self.screen_px()
        leaders = self.leaders()
        for i in range(len(leaders) - 1, -1, -1):
            path = self._path_for(leaders[i])
            if any(
                _dist_to_segment(local_pos, a, b) <= tol
                for a, b in zip(path, path[1:])
            ):
                return i
        return None

    def leader_bend_at(self, index: int, local_pos: QPointF) -> int | None:
        tol = (_HIT_PX + 2.0) * self.screen_px()
        for j, b in enumerate(self.leaders()[index].bends):
            q = self._to_local(b)
            if math.hypot(q.x() - local_pos.x(), q.y() - local_pos.y()) <= tol:
                return j
        return None

    def leaders_with_bend_added(
        self, index: int, local_pos: QPointF
    ) -> list[Leader]:
        """The list with a bend inserted on leader `index`, on the
        segment nearest `local_pos`, at the projection of that point (at
        the segment's middle when that falls on one of its ends)."""
        leaders = self.leaders()
        ld = leaders[index]
        path = self._path_for(ld)
        best = min(
            range(len(path) - 1),
            key=lambda k: _dist_to_segment(local_pos, path[k], path[k + 1]),
        )
        point = bend_point_on_segment(
            path[best],
            path[best + 1],
            local_pos,
            (_HIT_PX + 3.0) * self.screen_px(),
        )
        proj = self.mapToParent(point)
        bends = list(ld.bends)
        radii = ld.bend_radii()
        # Segment 0 runs from the start to the first bend (or target).
        bends.insert(best, (proj.x(), proj.y()))
        radii.insert(best, 0.0)
        leaders[index] = replace(ld, bends=tuple(bends), radii=tuple(radii))
        return leaders

    # ------------------------------------------------------------------
    # moves and document resize
    # ------------------------------------------------------------------
    def scale_leaders(self, s: float) -> None:
        if self.has_leaders():
            self.set_leaders([ld.scaled(s) for ld in self.leaders()])

    def itemChange(self, change, value):  # noqa: ANN001, N802
        # The leaders are fixed on the page while the item moves, so the
        # item's own (local) bounds change with its position.
        if (
            change == QGraphicsItem.ItemPositionChange
            and (self.has_leaders() or self.leader_preview() is not None)
        ):
            self.prepareGeometryChange()
        return super().itemChange(change, value)
