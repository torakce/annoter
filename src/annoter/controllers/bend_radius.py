"""Corner radius on the bends of any annotation (2026-09-28).

Every annotation with corners can round them, each with its own radius
(see `views/items/rounding.py` for the drawing):

    ("bend", i)          bend i of a line / arrow
    ("vertex", i)        vertex i of a polyline (inner ones) / polygon
    ("leader", l, i)     bend i of leader l of a text / GD&T frame

This module hides those three storages behind one vocabulary for the
context menu (radius of the clicked bend) and the inspector (one radius
for every bend). Setters here apply live, without undo: callers take a
`geom_snapshot()` first and push one ResizeCommand once the edit is
over -- the snapshot of each of those items includes its radii (lines,
polylines) or its leaders (texts, GD&T frames).

Radii are in item units (page pixels), like the geometry.
"""

from __future__ import annotations

from typing import Union

from PySide6.QtCore import QPointF

from annoter.views.items.leaders import LeaderHost
from annoter.views.items.lines import LineItem
from annoter.views.items.poly import _PolyItem

Corner = Union[tuple[str, int], tuple[str, int, int]]


def corners(item) -> list[Corner]:  # noqa: ANN001
    """Every corner of `item` that can be rounded, in drawing order."""
    if isinstance(item, LineItem):
        return [("bend", i) for i in range(len(item.bends()))]
    if isinstance(item, _PolyItem):
        return [("vertex", i) for i in item.corner_indices()]
    if isinstance(item, LeaderHost):
        return [
            ("leader", li, bi)
            for li, ld in enumerate(item.leaders())
            for bi in range(len(ld.bends))
        ]
    return []


def has_corners(item) -> bool:  # noqa: ANN001
    return bool(corners(item))


def corner_at(item, local_pos: QPointF) -> Corner | None:  # noqa: ANN001
    """The corner whose handle is under `local_pos` (item coordinates)."""
    if isinstance(item, LineItem):
        i = item.bend_at(local_pos)
        return None if i is None else ("bend", i)
    if isinstance(item, _PolyItem):
        i = item.vertex_at(local_pos)
        if i is None or i not in item.corner_indices():
            return None
        return ("vertex", i)
    if isinstance(item, LeaderHost):
        for li in range(len(item.leaders()) - 1, -1, -1):
            bi = item.leader_bend_at(li, local_pos)
            if bi is not None:
                return ("leader", li, bi)
    return None


def radius(item, corner: Corner) -> float:  # noqa: ANN001
    kind = corner[0]
    if kind == "leader":
        _k, li, bi = corner
        return item.leaders()[li].bend_radii()[bi]
    return item.bend_radii()[corner[1]]


def radii(item) -> list[float]:  # noqa: ANN001
    """The radius of every corner, in the order of `corners(item)`."""
    return [radius(item, c) for c in corners(item)]


def set_radius(item, corner: Corner, value: float) -> None:  # noqa: ANN001
    """Round one corner (live, no undo)."""
    value = max(0.0, float(value))
    kind = corner[0]
    if kind == "leader":
        _k, li, bi = corner
        leaders = item.leaders()
        leaders[li] = leaders[li].with_bend_radius(bi, value)
        item.set_leaders(leaders)
        return
    values = item.bend_radii()
    values[corner[1]] = value
    item.set_bend_radii(values)


def set_all_radii(item, value: float) -> None:  # noqa: ANN001
    """Round every corner of `item` with the same radius (live)."""
    value = max(0.0, float(value))
    if isinstance(item, LeaderHost) and not isinstance(
        item, (LineItem, _PolyItem)
    ):
        leaders = []
        for ld in item.leaders():
            for bi in range(len(ld.bends)):
                ld = ld.with_bend_radius(bi, value)
            leaders.append(ld)
        item.set_leaders(leaders)
        return
    values = item.bend_radii()
    for c in corners(item):
        values[c[1]] = value
    item.set_bend_radii(values)


def common_radius(item) -> float | None:  # noqa: ANN001
    """The radius all corners share, None when they differ (or none)."""
    values = radii(item)
    if not values:
        return None
    first = values[0]
    return first if all(abs(v - first) < 1e-6 for v in values) else None
