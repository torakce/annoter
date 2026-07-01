"""Shared "true geometry" helpers for annotation items.

`QGraphicsItem.boundingRect()` includes selection-handle padding (and,
for a few item types, a small fixed visual margin) which makes it
unsuitable for anything that needs the *actual* shape extents: align /
distribute, the live measurement HUD, the numeric geometry panel, smart
guides, and endpoint snapping all need the same tight rect. Duck-typing
on the methods items already expose (`content_rect`, `rect`,
`line_points`, `points`) avoids importing every concrete item class here
(and the import cycles that would invite).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF

from annoter.config import BASE_RENDER_DPI
from annoter.views.items.base import AnnotationItem


def px_to_pt(value: float) -> float:
    """Convert a page-pixel length (annotation coordinate space, fixed
    to `BASE_RENDER_DPI` regardless of the current hi-DPI re-render) to
    PDF points (1/72 in) -- the same unit the persistence layer writes.

    This is the annotation's own on-page size, not a calibrated
    real-world measurement (scale calibration is out of scope for v1).
    """
    return value * 72.0 / BASE_RENDER_DPI


def pt_to_px(value: float) -> float:
    """Inverse of `px_to_pt`."""
    return value * BASE_RENDER_DPI / 72.0


def item_local_rect(item: AnnotationItem) -> QRectF:
    """Best-effort true geometry rect, in the item's local coordinates."""
    if hasattr(item, "content_rect"):
        return item.content_rect()
    if hasattr(item, "rect"):
        return item.rect()
    if hasattr(item, "line_points"):
        p1, p2 = item.line_points()
        return QRectF(
            min(p1.x(), p2.x()),
            min(p1.y(), p2.y()),
            abs(p2.x() - p1.x()),
            abs(p2.y() - p1.y()),
        )
    if hasattr(item, "points"):
        pts = item.points()
        if not pts:
            return QRectF()
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        return QRectF(
            min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
        )
    return item.boundingRect()


def item_scene_rect(item: AnnotationItem) -> QRectF:
    """True geometry rect translated into scene (page-local) coordinates.

    Annotation items never rotate or scale (only `pos()` is used), so a
    plain translation is exact -- no need for `mapRectToScene`.
    """
    return item_local_rect(item).translated(item.pos())


def move_delta_for_rect(
    item: AnnotationItem, target_rect_pos: QPointF
) -> QPointF:
    """Delta to add to `item.pos()` so its local rect's top-left lands on
    `target_rect_pos` (scene coordinates)."""
    local = item_local_rect(item)
    current_scene_topleft = QPointF(
        local.x() + item.pos().x(), local.y() + item.pos().y()
    )
    return target_rect_pos - current_scene_topleft
