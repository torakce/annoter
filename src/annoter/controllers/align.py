"""Align & distribute: PowerPoint/Canva-style layout commands.

Pure geometry -- computes the (item, old_pos, new_pos) triples that the
caller feeds into `MoveAnnotationsCommand`, so the whole operation is a
single undo step, same as a manual drag.
"""

from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import QPointF

from annoter.controllers.geometry import item_scene_rect
from annoter.views.items.base import AnnotationItem


class AlignMode(Enum):
    LEFT = auto()
    CENTER_H = auto()
    RIGHT = auto()
    TOP = auto()
    MIDDLE_V = auto()
    BOTTOM = auto()
    DISTRIBUTE_H = auto()
    DISTRIBUTE_V = auto()


Move = tuple[AnnotationItem, QPointF, QPointF]


def compute_align_moves(
    items: list[AnnotationItem], mode: AlignMode
) -> list[Move]:
    """Return the moves for `mode`, applied to the *scene* bounding box
    of `items`' true geometry (see `controllers.geometry`).

    Fewer than 2 items -> no-op (nothing to align relative to). The
    distribute modes need at least 3 items to have any visible effect;
    with fewer they degrade gracefully to no-op moves.
    """
    if len(items) < 2:
        return []
    rects = {id(it): item_scene_rect(it) for it in items}

    if mode in (
        AlignMode.LEFT,
        AlignMode.CENTER_H,
        AlignMode.RIGHT,
        AlignMode.TOP,
        AlignMode.MIDDLE_V,
        AlignMode.BOTTOM,
    ):
        return _simple_align(items, rects, mode)
    if mode is AlignMode.DISTRIBUTE_H:
        return _distribute(items, rects, horizontal=True)
    if mode is AlignMode.DISTRIBUTE_V:
        return _distribute(items, rects, horizontal=False)
    return []


def _simple_align(items, rects, mode: AlignMode) -> list[Move]:
    lefts = [r.left() for r in rects.values()]
    rights = [r.right() for r in rects.values()]
    tops = [r.top() for r in rects.values()]
    bottoms = [r.bottom() for r in rects.values()]
    union_left, union_right = min(lefts), max(rights)
    union_top, union_bottom = min(tops), max(bottoms)

    moves: list[Move] = []
    for it in items:
        r = rects[id(it)]
        dx = dy = 0.0
        if mode is AlignMode.LEFT:
            dx = union_left - r.left()
        elif mode is AlignMode.RIGHT:
            dx = union_right - r.right()
        elif mode is AlignMode.CENTER_H:
            dx = (union_left + union_right) / 2.0 - r.center().x()
        elif mode is AlignMode.TOP:
            dy = union_top - r.top()
        elif mode is AlignMode.BOTTOM:
            dy = union_bottom - r.bottom()
        elif mode is AlignMode.MIDDLE_V:
            dy = (union_top + union_bottom) / 2.0 - r.center().y()
        if dx or dy:
            old = QPointF(it.pos())
            moves.append((it, old, QPointF(old.x() + dx, old.y() + dy)))
    return moves


def _distribute(items, rects, *, horizontal: bool) -> list[Move]:
    def lo(r):
        return r.left() if horizontal else r.top()

    def hi(r):
        return r.right() if horizontal else r.bottom()

    def size(r):
        return r.width() if horizontal else r.height()

    ordered = sorted(items, key=lambda it: lo(rects[id(it)]))
    n = len(ordered)
    if n < 3:
        return []

    span_start = lo(rects[id(ordered[0])])
    span_end = hi(rects[id(ordered[-1])])
    total_size = sum(size(rects[id(it)]) for it in ordered)
    gap = (span_end - span_start - total_size) / (n - 1)

    moves: list[Move] = []
    cursor = span_start
    for i, it in enumerate(ordered):
        r = rects[id(it)]
        if i == 0 or i == n - 1:
            cursor += size(r) + gap
            continue
        delta = cursor - lo(r)
        old = QPointF(it.pos())
        new = (
            QPointF(old.x() + delta, old.y())
            if horizontal
            else QPointF(old.x(), old.y() + delta)
        )
        moves.append((it, old, new))
        cursor += size(r) + gap
    return moves
