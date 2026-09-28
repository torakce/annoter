"""Rounded corners on bent paths: a radius on each bend (2026-09-28).

Lines and arrows (at their bends), polylines (at inner vertices),
polygons (at every vertex) and the leaders of texts and GD&T frames can
round each corner with its own radius -- a circular fillet tangent to
both segments, like a CATIA polyline's corner radius.

A radius larger than the corner can hold is reduced so the fillet never
eats more than half of either adjacent segment (two fillets on one
segment then meet at worst in its middle). The source geometry -- the
sharp vertices and one radius per vertex -- is what items store and
save; this module only turns it into what is drawn: a QPainterPath with
true arcs on screen, or a polyline with the arcs sampled for the PDF
(external viewers draw vertices, not arcs).
"""

from __future__ import annotations

import math
from typing import Sequence

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath

# Sampling of an arc for PDF vertices: one point every this many degrees.
_SAMPLE_DEG = 15.0


def _fillet(
    prev: QPointF, corner: QPointF, nxt: QPointF, radius: float
) -> tuple[QPointF, QPointF, QPointF, float] | None:
    """(tangent in, tangent out, center, radius) of the fillet at
    `corner`, or None when there is nothing to round (no radius, a
    straight or folded-back corner, a zero-length segment)."""
    if radius <= 0.0:
        return None
    ax, ay = prev.x() - corner.x(), prev.y() - corner.y()
    bx, by = nxt.x() - corner.x(), nxt.y() - corner.y()
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    if la < 1e-9 or lb < 1e-9:
        return None
    ux, uy = ax / la, ay / la
    vx, vy = bx / lb, by / lb
    cos_phi = max(-1.0, min(1.0, ux * vx + uy * vy))
    phi = math.acos(cos_phi)  # angle between the two segments
    if phi < 1e-3 or phi > math.pi - 1e-3:
        return None
    half = phi / 2.0
    d = radius / math.tan(half)  # corner -> tangent point
    d = min(d, la / 2.0, lb / 2.0)
    r = d * math.tan(half)
    if r < 1e-6:
        return None
    t_in = QPointF(corner.x() + ux * d, corner.y() + uy * d)
    t_out = QPointF(corner.x() + vx * d, corner.y() + vy * d)
    bx_, by_ = ux + vx, uy + vy
    lbis = math.hypot(bx_, by_)
    dist_c = r / math.sin(half)
    center = QPointF(
        corner.x() + bx_ / lbis * dist_c, corner.y() + by_ / lbis * dist_c
    )
    return t_in, t_out, center, r


def _angle_deg(center: QPointF, p: QPointF) -> float:
    """Qt arc angle of `p` around `center` (y axis pointing down)."""
    return math.degrees(math.atan2(-(p.y() - center.y()), p.x() - center.x()))


def _sweep(a_from: float, a_to: float) -> float:
    s = (a_to - a_from) % 360.0
    return s - 360.0 if s > 180.0 else s


def _corner_indices(n: int, closed: bool) -> range:
    return range(n) if closed else range(1, n - 1)


def has_rounding(radii: Sequence[float]) -> bool:
    return any(r > 0.0 for r in radii)


def rounded_path(
    points: Sequence[QPointF],
    radii: Sequence[float],
    closed: bool = False,
) -> QPainterPath:
    """The path through `points`, each corner i rounded with radii[i]
    (missing / zero radii: sharp corners). The ends of an open path are
    never rounded."""
    path = QPainterPath()
    n = len(points)
    if n == 0:
        return path
    radii = list(radii) + [0.0] * max(0, n - len(radii))
    fillets = {
        i: _fillet(points[i - 1], points[i], points[(i + 1) % n], radii[i])
        for i in _corner_indices(n, closed)
        if n >= 3 or not closed
    }
    if closed:
        f0 = fillets.get(0)
        path.moveTo(f0[1] if f0 else points[0])
        order = list(range(1, n)) + [0]
    else:
        path.moveTo(points[0])
        order = list(range(1, n))
    for i in order:
        f = fillets.get(i)
        if f is None:
            path.lineTo(points[i])
            continue
        t_in, t_out, center, r = f
        path.lineTo(t_in)
        a1 = _angle_deg(center, t_in)
        a2 = _angle_deg(center, t_out)
        path.arcTo(
            QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r),
            a1,
            _sweep(a1, a2),
        )
    if closed:
        path.closeSubpath()
    return path


def rounded_points(
    points: Sequence[QPointF],
    radii: Sequence[float],
    closed: bool = False,
) -> list[QPointF]:
    """`rounded_path` as a polyline: each arc sampled every few degrees.
    For PDF vertices (Line / PolyLine / Polygon annotations)."""
    n = len(points)
    radii = list(radii) + [0.0] * max(0, n - len(radii))
    out: list[QPointF] = []
    for i in range(n):
        corner_ok = closed or 0 < i < n - 1
        f = (
            _fillet(points[i - 1], points[i], points[(i + 1) % n], radii[i])
            if corner_ok and n >= 3
            else None
        )
        if f is None:
            out.append(QPointF(points[i]))
            continue
        t_in, t_out, center, r = f
        a1 = math.atan2(t_in.y() - center.y(), t_in.x() - center.x())
        a2 = math.atan2(t_out.y() - center.y(), t_out.x() - center.x())
        sweep = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
        steps = max(2, int(math.ceil(abs(math.degrees(sweep)) / _SAMPLE_DEG)))
        for k in range(steps + 1):
            a = a1 + sweep * k / steps
            out.append(
                QPointF(center.x() + r * math.cos(a), center.y() + r * math.sin(a))
            )
    return out
