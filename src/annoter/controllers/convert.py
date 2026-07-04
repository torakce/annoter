"""Conversions between related annotation kinds (Discussion #1, item 3).

Related tools are merged in the palette (rectangle/cloud, line/arrow/
callout, polyline/polygon); the variant is switched after the fact from
the Properties dock. Each function builds a **detached** converted item
(no scene, no parent) mirroring the source's geometry and shared style;
the caller swaps it in via `ReplaceAnnotationCommand` so the change is a
single undo step.

Conversions are lossy where the kinds genuinely differ (a cloud has no
text label, an arrow has no text at all): what cannot be represented is
dropped, deliberately and documented, rather than smuggled along.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from annoter.model.styles import EndStyle
from annoter.views.items.base import AnnotationItem
from annoter.views.items.callout import CalloutItem
from annoter.views.items.lines import ArrowItem, LineItem
from annoter.views.items.poly import PolygonItem, PolylineItem
from annoter.views.items.shapes import (
    CloudItem,
    RectangleItem,
    _ShapeItem,
)


def _copy_fill(src, dst) -> None:  # noqa: ANN001
    dst.set_fill_enabled(src.fill_enabled())
    dst.set_fill_color(src.fill_color())
    dst.set_fill_opacity(src.fill_opacity())


def rect_to_cloud(item: RectangleItem) -> CloudItem:
    c = CloudItem(item.rect())
    item._copy_base_style_into(c)
    _copy_fill(item, c)
    # The text label and corner radius have no cloud equivalent.
    return c


def cloud_to_rect(item: CloudItem) -> RectangleItem:
    r = RectangleItem(item.rect())
    item._copy_base_style_into(r)
    _copy_fill(item, r)
    return r


def polyline_to_polygon(item: PolylineItem) -> PolygonItem:
    p = PolygonItem(item.points())
    item._copy_base_style_into(p)
    return p


def polygon_to_polyline(item: PolygonItem) -> PolylineItem:
    p = PolylineItem(item.points())
    item._copy_base_style_into(p)
    # The fill has no open-path equivalent.
    return p


def line_to_arrow(item: LineItem) -> ArrowItem:
    p1, p2 = item.line_points()
    a = ArrowItem(p1, p2)
    item._copy_base_style_into(a)
    item._copy_line_extras_into(a)  # bends + end labels
    return a


def arrow_to_line(item: ArrowItem) -> LineItem:
    p1, p2 = item.line_points()
    line = LineItem(p1, p2)
    item._copy_base_style_into(line)
    item._copy_line_extras_into(line)
    return line


def callout_to_arrow(item: CalloutItem) -> ArrowItem:
    """Callout -> arrow: leader becomes the shaft, tip keeps the head.
    The text is dropped (an arrow carries none)."""
    conn = item.connection_point()
    tip = item.tip()
    sp1 = QPointF(conn.x() + item.pos().x(), conn.y() + item.pos().y())
    sp2 = QPointF(tip.x() + item.pos().x(), tip.y() + item.pos().y())
    a = ArrowItem(sp1, sp2)
    a.set_color(item.color())
    a.set_stroke(item.stroke())
    a.set_dash_style(item.dash_style())
    a.set_end_end(EndStyle.OPEN_ARROW)
    return a


def convert_shape_outline(
    item: _ShapeItem, cloudy: bool
) -> AnnotationItem | None:
    """Outline variant for rect-footprint shapes: straight <-> cloud.
    Returns None when the item is already the requested variant."""
    if cloudy and isinstance(item, RectangleItem):
        return rect_to_cloud(item)
    if not cloudy and isinstance(item, CloudItem):
        return cloud_to_rect(item)
    return None


def convert_poly_closed(
    item: AnnotationItem, closed: bool
) -> AnnotationItem | None:
    """Closed variant for multi-vertex paths. None when already there."""
    if closed and isinstance(item, PolylineItem):
        return polyline_to_polygon(item)
    if not closed and isinstance(item, PolygonItem):
        return polygon_to_polyline(item)
    return None
