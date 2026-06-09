"""QUndoCommand subclasses: every scene mutation flows through here.

Widgets must NEVER mutate the scene directly. Stack capped at
`config.UNDO_STACK_LIMIT` commands.

Commands:
    AddAnnotationCommand
    DeleteAnnotationsCommand
    MoveAnnotationsCommand
    ChangeColorCommand
    ChangeStrokeCommand
    ChangeGdtCommand           (M3, defined here as a stub)
"""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QUndoCommand
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene

from annoter.views.items.base import AnnotationItem


# ----------------------------------------------------------------------
# Add / Delete
# ----------------------------------------------------------------------


class AddAnnotationCommand(QUndoCommand):
    """Adds a single AnnotationItem as a child of the page item."""

    def __init__(
        self,
        scene: QGraphicsScene,
        parent_item: QGraphicsItem,
        item: AnnotationItem,
        label: str = "Add annotation",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._scene = scene
        self._parent_item = parent_item
        self._item = item

    def redo(self) -> None:
        if self._item.scene() is None:
            self._scene.addItem(self._item)
        self._item.setParentItem(self._parent_item)

    def undo(self) -> None:
        self._item.setSelected(False)
        if self._item.scene() is not None:
            self._scene.removeItem(self._item)


class DeleteAnnotationsCommand(QUndoCommand):
    """Removes one or more AnnotationItems from their page parent."""

    def __init__(
        self,
        scene: QGraphicsScene,
        items: Iterable[AnnotationItem],
        label: str = "Delete annotation(s)",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._scene = scene
        self._items: list[AnnotationItem] = list(items)
        self._parents: list[QGraphicsItem | None] = [
            it.parentItem() for it in self._items
        ]

    def redo(self) -> None:
        for it in self._items:
            it.setSelected(False)
            if it.scene() is not None:
                self._scene.removeItem(it)

    def undo(self) -> None:
        for it, parent in zip(self._items, self._parents):
            if it.scene() is None:
                self._scene.addItem(it)
            it.setParentItem(parent)


# ----------------------------------------------------------------------
# Move
# ----------------------------------------------------------------------


class MoveAnnotationsCommand(QUndoCommand):
    """Moves one or more items by remembering old/new positions."""

    def __init__(
        self,
        moves: Sequence[tuple[AnnotationItem, QPointF, QPointF]],
        label: str = "Move annotation(s)",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._moves = [
            (it, QPointF(old), QPointF(new)) for it, old, new in moves
        ]

    def redo(self) -> None:
        for it, _old, new in self._moves:
            it.setPos(new)

    def undo(self) -> None:
        for it, old, _new in self._moves:
            it.setPos(old)


# ----------------------------------------------------------------------
# Style changes
# ----------------------------------------------------------------------


class ChangeColorCommand(QUndoCommand):
    def __init__(
        self,
        items: Iterable[AnnotationItem],
        new_color: QColor,
        label: str = "Change color",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._items: list[AnnotationItem] = list(items)
        self._new = QColor(new_color)
        self._old: list[QColor] = [it.color() for it in self._items]

    def redo(self) -> None:
        for it in self._items:
            it.set_color(self._new)

    def undo(self) -> None:
        for it, c in zip(self._items, self._old):
            it.set_color(c)


class ChangeStrokeCommand(QUndoCommand):
    def __init__(
        self,
        items: Iterable[AnnotationItem],
        new_width: float,
        label: str = "Change stroke",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._items: list[AnnotationItem] = list(items)
        self._new = float(new_width)
        self._old: list[float] = [it.stroke() for it in self._items]

    def redo(self) -> None:
        for it in self._items:
            it.set_stroke(self._new)

    def undo(self) -> None:
        for it, w in zip(self._items, self._old):
            it.set_stroke(w)


# ----------------------------------------------------------------------
# GD&T
# ----------------------------------------------------------------------


class ChangePropsCommand(QUndoCommand):
    """Apply per-item property changes by calling `set_<name>(value)`.

    Each entry is `(item, prop_name, old_value, new_value)`. Multiple
    properties on the same item are flushed in order, which makes a
    single user gesture (e.g. "toggle fill + pick a fill color") one
    undo step.
    """

    def __init__(
        self,
        changes: Iterable[tuple[AnnotationItem, str, object, object]],
        label: str = "Edit properties",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._changes: list[tuple[AnnotationItem, str, object, object]] = list(
            changes
        )

    @staticmethod
    def _apply(item: AnnotationItem, name: str, value: object) -> None:
        setter = getattr(item, f"set_{name}", None)
        if setter is not None:
            setter(value)

    def redo(self) -> None:
        for item, name, _old, new in self._changes:
            self._apply(item, name, new)

    def undo(self) -> None:
        for item, name, old, _new in self._changes:
            self._apply(item, name, old)


class ResizeCommand(QUndoCommand):
    """Generic geometry-change command driven by `apply_geom` snapshots.

    Each item type defines what its snapshot looks like (a QRectF for
    shapes, a (p1, p2) tuple for lines, ...). The command shuttles
    opaque snapshots between item and undo stack so the same plumbing
    handles every resizable item type.
    """

    def __init__(
        self,
        item: AnnotationItem,
        old_snapshot: object,
        new_snapshot: object,
        label: str = "Resize annotation",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._item = item
        self._old = old_snapshot
        self._new = new_snapshot

    def redo(self) -> None:
        self._item.apply_geom(self._new)

    def undo(self) -> None:
        self._item.apply_geom(self._old)


class ChangeGdtCommand(QUndoCommand):
    """Swap the GdtState of a GdtAnnotationItem (round-trip via undo)."""

    def __init__(
        self,
        item: AnnotationItem,
        old_state,
        new_state,
        label: str = "Edit GD&T",
        parent: QUndoCommand | None = None,
    ) -> None:
        super().__init__(label, parent)
        self._item = item
        self._old = old_state
        self._new = new_state

    def redo(self) -> None:
        if hasattr(self._item, "apply_gdt_state"):
            self._item.apply_gdt_state(self._new)

    def undo(self) -> None:
        if hasattr(self._item, "apply_gdt_state"):
            self._item.apply_gdt_state(self._old)
