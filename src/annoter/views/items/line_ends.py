"""Extremity shapes drawn at the end of a line: arrowheads, datum
triangles, ticks, dots... Shared by ArrowItem and by the leaders of
texts and GD&T frames (kept apart from lines.py so the leader module
does not depend on the line items)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPolygonF

from annoter.model.styles import EndStyle

# Arrowhead half-angle.
HEAD_HALF_ANGLE = math.radians(22.0)


def bend_point_on_segment(
    a: QPointF, b: QPointF, near: QPointF, gap: float
) -> QPointF:
    """Where a new bend point goes on segment a-b for a click at `near`:
    the click's projection, unless that lands within `gap` of either end
    -- a right-click on an endpoint projects onto the endpoint itself,
    and the new point would sit under the handle, invisible -- in which
    case the middle of the segment."""
    abx, aby = b.x() - a.x(), b.y() - a.y()
    length = math.hypot(abx, aby)
    if length < 1e-9:
        return QPointF(a)
    t = ((near.x() - a.x()) * abx + (near.y() - a.y()) * aby) / (length**2)
    t = max(0.0, min(1.0, t))
    if t * length < gap or (1.0 - t) * length < gap:
        t = 0.5
    return QPointF(a.x() + t * abx, a.y() + t * aby)


def draw_line_end(
    painter,  # noqa: ANN001
    anchor: QPointF,
    towards: QPointF,
    style: EndStyle,
    size: float,
    color: QColor,
) -> None:
    """Draw the extremity `style` at `anchor`, oriented along
    anchor->towards, `size` long, with the painter's current pen.

    Shared by arrows and by the leaders of texts and GD&T frames."""
    if style is EndStyle.NONE:
        return
    dx = towards.x() - anchor.x()
    dy = towards.y() - anchor.y()
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    ang = math.atan2(dy, dx)

    if style in (EndStyle.OPEN_ARROW, EndStyle.CLOSED_ARROW):
        a1 = ang - HEAD_HALF_ANGLE
        a2 = ang + HEAD_HALF_ANGLE
        h1 = QPointF(
            anchor.x() + size * math.cos(a1),
            anchor.y() + size * math.sin(a1),
        )
        h2 = QPointF(
            anchor.x() + size * math.cos(a2),
            anchor.y() + size * math.sin(a2),
        )
        if style is EndStyle.CLOSED_ARROW:
            painter.setBrush(color)
            painter.drawPolygon(QPolygonF([anchor, h1, h2]))
        else:
            # Open chevron: two barbs only -- drawPolygon would close
            # the triangle and add a bar across the back of the head.
            painter.setBrush(Qt.NoBrush)
            painter.drawPolyline(QPolygonF([h1, anchor, h2]))
        return

    if style in (EndStyle.TRIANGLE, EndStyle.TRIANGLE_FILLED):
        # GD&T datum-feature triangle (ISO 5459): flat base sitting
        # ON the endpoint, perpendicular to the shaft, apex pointing
        # back along the line so the leader meets the apex.
        apex = QPointF(
            anchor.x() + size * math.cos(ang),
            anchor.y() + size * math.sin(ang),
        )
        half_base = size / math.sqrt(3.0)  # equilateral proportions
        perp = ang + math.pi / 2
        b1 = QPointF(
            anchor.x() + half_base * math.cos(perp),
            anchor.y() + half_base * math.sin(perp),
        )
        b2 = QPointF(
            anchor.x() - half_base * math.cos(perp),
            anchor.y() - half_base * math.sin(perp),
        )
        if style is EndStyle.TRIANGLE_FILLED:
            painter.setBrush(color)
        else:
            # Opaque white (not transparent) so the shaft does not
            # show through the "hollow" triangle -- same convention
            # as the GD&T frame cells.
            painter.setBrush(QColor("#FFFFFF"))
        painter.drawPolygon(QPolygonF([b1, apex, b2]))
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
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([tip1, tip3, tip2, tip4]))
        return

    if style is EndStyle.CIRCLE:
        r = size * 0.35
        painter.setBrush(color)
        painter.drawEllipse(anchor, r, r)
        return

    if style is EndStyle.SQUARE:
        half = size * 0.3
        painter.setBrush(color)
        painter.drawRect(
            QRectF(
                anchor.x() - half,
                anchor.y() - half,
                2 * half,
                2 * half,
            )
        )
        return
