"""Stacking order of the annotations on a page (Lot J).

The annotations of a page are the children of its pixmap item and all
keep the default Z value, so their stacking order is their order among
the page's children -- the order `childItems()` returns, bottom first,
and the order the save path writes them into the PDF. Reordering uses
`QGraphicsItem.stackBefore`, never `setZValue`: with a single Z value a
newly drawn annotation still lands on top, and nothing else in the
scene has to know about levels.

One step ("Bring Forward" / "Send Backward") moves the selection past
the next annotation that OVERLAPS it, the way Inkscape raises and
lowers: stepping past an annotation on the other side of the sheet
would change nothing on screen, so each click always has a visible
effect -- ten clicks go ten levels down through a pile.
"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtWidgets import QGraphicsItem

from annoter.controllers.geometry import item_scene_rect
from annoter.views.items.base import AnnotationItem

# Tolerance, in page pixels, so a horizontal line (zero-height rect)
# still counts as overlapping what it crosses.
_OVERLAP_SLACK = 2.0

RAISE = "raise"
LOWER = "lower"
TO_FRONT = "front"
TO_BACK = "back"
MOVES = (RAISE, LOWER, TO_FRONT, TO_BACK)


def page_stack(parent: QGraphicsItem | None) -> list[AnnotationItem]:
    """The annotations under `parent`, bottom first."""
    if parent is None:
        return []
    return [c for c in parent.childItems() if isinstance(c, AnnotationItem)]


def apply_stack(order: list[AnnotationItem]) -> None:
    """Restack siblings so they read `order` bottom to top.

    Chained from the top down: each item is placed just below the one
    that must sit above it, so the last pair settles first and the
    whole run ends up contiguous and in order.
    """
    for i in range(len(order) - 2, -1, -1):
        order[i].stackBefore(order[i + 1])
    # stackBefore reorders without scheduling a repaint: without this
    # the canvas kept the old order until something else redrew it
    # (user report: the change showed only after clicking elsewhere).
    for item in order:
        item.update()


def overlaps(a: AnnotationItem, b: AnnotationItem) -> bool:
    ra = item_scene_rect(a).adjusted(
        -_OVERLAP_SLACK, -_OVERLAP_SLACK, _OVERLAP_SLACK, _OVERLAP_SLACK
    )
    rb = item_scene_rect(b).adjusted(
        -_OVERLAP_SLACK, -_OVERLAP_SLACK, _OVERLAP_SLACK, _OVERLAP_SLACK
    )
    return ra.intersects(rb)


def restacked(
    order: list[AnnotationItem],
    selected: Iterable[AnnotationItem],
    move: str,
    overlap: Callable[[AnnotationItem, AnnotationItem], bool] = overlaps,
) -> list[AnnotationItem]:
    """New bottom-to-top order after `move` is applied to `selected`.

    Selected items keep their relative order. An item with no
    overlapping neighbour in the requested direction stays where it is.
    """
    if move not in MOVES:
        raise ValueError(move)
    chosen = [it for it in order if it in set(selected)]
    if not chosen:
        return list(order)
    picked = set(chosen)
    if move == TO_FRONT:
        return [it for it in order if it not in picked] + chosen
    if move == TO_BACK:
        return chosen + [it for it in order if it not in picked]

    new = list(order)
    if move == RAISE:
        # Topmost first, so a selected item never jumps over another
        # selected one that has not moved yet.
        for item in reversed(chosen):
            i = new.index(item)
            target = next(
                (
                    j
                    for j in range(i + 1, len(new))
                    if new[j] not in picked and overlap(item, new[j])
                ),
                None,
            )
            if target is None:
                continue
            new.pop(i)
            new.insert(target, item)  # right above new[target]
    else:
        for item in chosen:
            i = new.index(item)
            target = next(
                (
                    j
                    for j in range(i - 1, -1, -1)
                    if new[j] not in picked and overlap(item, new[j])
                ),
                None,
            )
            if target is None:
                continue
            new.pop(i)
            new.insert(target, item)  # right below new[target]
    return new


def can_restack(
    parent: QGraphicsItem | None,
    selected: Iterable[AnnotationItem],
    move: str,
) -> bool:
    """Whether `move` would change anything for this selection."""
    order = page_stack(parent)
    return restacked(order, selected, move) != order
