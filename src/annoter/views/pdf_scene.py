"""PdfScene: holds the current page's pixmap and its annotation children.

One page at a time (no continuous scroll). Repopulated on page switch.
The scene also dispatches mouse events to the active tool: drawing
tools build a new annotation; the SELECT tool falls back to the default
QGraphicsScene behavior (selection / move).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
)
from PySide6.QtGui import QUndoStack

from annoter.controllers.commands import (
    AddAnnotationCommand,
    MoveAnnotationsCommand,
    ResizeCommand,
)
from annoter.model.styles import HandleRole
from annoter.controllers.tools import Tool, ToolController
from annoter.views.items import (
    ArrowItem,
    CalloutItem,
    CloudItem,
    EllipseItem,
    FreehandItem,
    LineItem,
    PolygonItem,
    PolylineItem,
    RectangleItem,
    StampItem,
    TextAnnotationItem,
)
from annoter.views.items.base import AnnotationItem


class PdfScene(QGraphicsScene):
    """One page at a time, with tool-driven mouse dispatch."""

    annotationsChanged = Signal()  # emitted after add / delete
    gdtPlacementRequested = Signal(QPointF)  # GD&T tool clicked on the page
    notePlacementRequested = Signal(QPointF)  # sticky-note tool clicked

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._page_item: QGraphicsPixmapItem | None = None
        # Hi-res viewport overlay: child of the page item, below the
        # annotations (negative Z among siblings), purely visual.
        self._hires_item: QGraphicsPixmapItem | None = None
        self._tool_controller: ToolController | None = None
        self._undo_stack: QUndoStack | None = None

        # Drawing state.
        self._draft_item: AnnotationItem | None = None
        self._draft_origin: QPointF | None = None

        # Multi-click drawing state (polyline / polygon): the draft item
        # shows the committed vertices plus a floating one tracking the
        # cursor; clicks append, double-click / Enter finishes.
        self._poly_draft: AnnotationItem | None = None
        self._poly_points: list[QPointF] = []

        # Move tracking (SELECT tool).
        self._move_origins: dict[AnnotationItem, QPointF] = {}

        # Resize tracking (handle drag on a selected item).
        self._resize_item: AnnotationItem | None = None
        self._resize_role: HandleRole | None = None
        self._resize_snapshot: object = None

        # Ctrl+drag duplicate: a custom drag mode that bypasses Qt's
        # default selection handling so we can move clones predictably.
        self._dup_active: bool = False
        self._dup_origin: QPointF | None = None
        self._dup_items: list[AnnotationItem] = []
        self._dup_start_positions: list[QPointF] = []
        # Ctrl+press is ambiguous until the cursor moves: a plain click
        # toggles the item's selection (multi-select), a drag past the
        # platform drag threshold duplicates. Cloning is deferred.
        self._dup_pending_item: AnnotationItem | None = None
        self._dup_pending_scene: QPointF | None = None
        self._dup_pending_screen: QPoint | None = None

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    def set_tool_controller(self, controller: ToolController) -> None:
        self._tool_controller = controller

    def set_undo_stack(self, stack: QUndoStack) -> None:
        self._undo_stack = stack

    # ------------------------------------------------------------------
    # page lifecycle
    # ------------------------------------------------------------------
    def set_page_pixmap(self, pixmap: QPixmap) -> None:
        """Swap the displayed pixmap. Reuses the existing page item so
        annotation children remain attached. Use `clear_page` for full
        teardown (e.g., when closing the document) and `detach_children`
        / `attach_children` for cross-page lifecycle.
        """
        if self._page_item is None:
            self._page_item = QGraphicsPixmapItem(pixmap)
            self._page_item.setTransformationMode(Qt.SmoothTransformation)
            self.addItem(self._page_item)
        else:
            self._page_item.setPixmap(pixmap)
        # The overlay belongs to the previous raster (possibly another
        # page); MainWindow re-creates it after its debounce.
        self.clear_hires_overlay()
        self.setSceneRect(self._page_item.boundingRect())

    def page_item(self) -> QGraphicsPixmapItem | None:
        return self._page_item

    def set_hires_overlay(self, pixmap: QPixmap, pos: QPointF) -> None:
        """Show `pixmap` at `pos` (page-local logical coords) over the page."""
        if self._page_item is None:
            return
        if self._hires_item is None:
            item = QGraphicsPixmapItem(self._page_item)
            item.setTransformationMode(Qt.SmoothTransformation)
            item.setZValue(-1.0)
            item.setAcceptedMouseButtons(Qt.NoButton)
            self._hires_item = item
        self._hires_item.setPixmap(pixmap)
        self._hires_item.setPos(pos)

    def clear_hires_overlay(self) -> None:
        if self._hires_item is None:
            return
        if self._hires_item.scene() is not None:
            self.removeItem(self._hires_item)
        self._hires_item = None

    def clear_page(self) -> None:
        self.clear()
        self._page_item = None
        self._hires_item = None
        self._draft_item = None
        self._draft_origin = None
        self._poly_draft = None
        self._poly_points = []
        self._move_origins.clear()
        self._resize_item = None
        self._resize_role = None
        self._resize_snapshot = None
        self._dup_active = False
        self._dup_origin = None
        self._dup_items = []
        self._dup_start_positions = []
        self._clear_dup_pending()

    def detach_children(self) -> list[AnnotationItem]:
        """Remove annotation children from the page item and return them.

        Used by MainWindow when switching pages: each page keeps its own
        bucket of annotations until persistence (M4).
        """
        if self._page_item is None:
            return []
        # A half-placed multi-click draft must not be persisted as a real
        # annotation when the user navigates away.
        self._discard_poly_draft()
        kids: list[AnnotationItem] = []
        for child in list(self._page_item.childItems()):
            if isinstance(child, AnnotationItem):
                child.setSelected(False)
                self.removeItem(child)
                kids.append(child)
        return kids

    def attach_children(self, items: list[AnnotationItem]) -> None:
        if self._page_item is None:
            return
        for it in items:
            if it.scene() is None:
                self.addItem(it)
            it.setParentItem(self._page_item)

    # ------------------------------------------------------------------
    # cancel / nudge helpers used by the view
    # ------------------------------------------------------------------
    def cancel_current_action(self) -> None:
        """Abort an in-progress draft, drop selection, return to Select.

        Bound to the Escape key. Safe to call any time -- a no-op when
        no draft / no selection.
        """
        if self._draft_item is not None:
            try:
                self.removeItem(self._draft_item)
            except Exception:
                pass
            self._draft_item = None
            self._draft_origin = None
        self._discard_poly_draft()
        self._clear_dup_pending()
        for it in list(self.selectedItems()):
            it.setSelected(False)
        if self._tool_controller is not None:
            self._tool_controller.set_tool(Tool.SELECT)

    def nudge_selection(self, dx: float, dy: float) -> None:
        """Translate the current selection by (dx, dy), undoably."""
        items = [
            it
            for it in self.selectedItems()
            if isinstance(it, AnnotationItem)
        ]
        if not items:
            return
        moves: list[tuple[AnnotationItem, QPointF, QPointF]] = []
        for it in items:
            old = QPointF(it.pos())
            new = QPointF(old.x() + dx, old.y() + dy)
            moves.append((it, old, new))
        cmd = MoveAnnotationsCommand(moves, label="Nudge annotation(s)")
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()

    # ------------------------------------------------------------------
    # event dispatch
    # ------------------------------------------------------------------
    def _current_tool(self) -> Tool:
        if self._tool_controller is None:
            return Tool.SELECT
        return self._tool_controller.tool()

    def _is_drawing_tool(self, tool: Tool) -> bool:
        return tool not in (Tool.SELECT,)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._page_item is None:
            super().mousePressEvent(event)
            return

        tool = self._current_tool()
        # Switching away from a poly tool mid-draft commits what's there.
        if self._poly_draft is not None and tool not in (
            Tool.POLYLINE,
            Tool.POLYGON,
        ):
            self.finish_poly_draft()
        if not self._is_drawing_tool(tool):
            # Resize: hit-test handles on the topmost already-selected
            # item under the cursor. Must run before super() because
            # the default handler would start a move drag.
            hit = self._hit_resize_handle(event.scenePos())
            if hit is not None:
                item, role = hit
                self._resize_item = item
                self._resize_role = role
                self._resize_snapshot = item.geom_snapshot()
                event.accept()
                return
            # Shift+click toggles the clicked annotation in and out of
            # the current selection (Qt only does this for Ctrl, which
            # we reserve for duplicate-drag).
            if event.modifiers() & Qt.ShiftModifier:
                clicked = self._topmost_annotation_at(event.scenePos())
                if clicked is not None:
                    clicked.setSelected(not clicked.isSelected())
                    event.accept()
                    return
            # Ctrl+press on an annotation: defer the decision -- a drag
            # duplicates, a plain click toggles the selection. We
            # short-circuit Qt's default either way so its own Ctrl
            # handling does not fight the duplicate.
            if event.modifiers() & Qt.ControlModifier:
                clicked = self._topmost_annotation_at(event.scenePos())
                if clicked is not None:
                    self._dup_pending_item = clicked
                    self._dup_pending_scene = QPointF(event.scenePos())
                    self._dup_pending_screen = QPoint(event.screenPos())
                    event.accept()
                    return
            super().mousePressEvent(event)
            self._capture_move_origins()
            return

        pos = event.scenePos()
        if not self._page_item.boundingRect().contains(pos):
            super().mousePressEvent(event)
            return

        if tool is Tool.TEXT:
            self._spawn_text_at(pos)
            event.accept()
            return

        if tool is Tool.GDT:
            # Defer to MainWindow: spawns a draft frame and opens the
            # in-place editor; the commit pushes the Add command.
            self.gdtPlacementRequested.emit(pos)
            event.accept()
            return

        if tool is Tool.STICKY_NOTE:
            # Defer to MainWindow: spawns a draft note and opens the
            # floating note editor; commit pushes the Add command.
            self.notePlacementRequested.emit(pos)
            event.accept()
            return

        if tool is Tool.STAMP:
            # One-click placement of a default stamp; the user re-types
            # the label / recolors it in the Properties dock.
            item = StampItem(pos)
            item.setParentItem(self._page_item)
            self._push_add(item)
            event.accept()
            return

        if tool in (Tool.POLYLINE, Tool.POLYGON):
            self._poly_click(tool, pos)
            event.accept()
            return

        self._draft_origin = pos
        self._draft_item = self._make_draft_item(tool, pos)
        if self._draft_item is not None:
            self._draft_item.setParentItem(self._page_item)
            self._apply_current_style(self._draft_item)
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._poly_draft is not None:
            pos = event.scenePos()
            if (
                event.modifiers() & Qt.ShiftModifier
                and self._poly_points
            ):
                pos = self._snap_angle(self._poly_points[-1], pos, 45.0)
            self._poly_draft.set_points(self._poly_points + [pos])
            event.accept()
            return
        if self._resize_item is not None and self._resize_role is not None:
            local = self._resize_item.mapFromScene(event.scenePos())
            if event.modifiers() & Qt.ShiftModifier:
                local = self._constrain_resize(
                    self._resize_item, self._resize_role, local
                )
            self._resize_item.apply_resize(self._resize_role, local)
            event.accept()
            return
        if self._dup_pending_item is not None:
            moved = (
                event.screenPos() - self._dup_pending_screen
            ).manhattanLength()
            if moved >= QApplication.startDragDistance():
                item = self._dup_pending_item
                origin = self._dup_pending_scene
                self._clear_dup_pending()
                self._begin_duplicate_drag(item, origin)
                # Catch up with the distance already travelled.
                delta = event.scenePos() - origin
                for it, start in zip(
                    self._dup_items, self._dup_start_positions
                ):
                    it.setPos(start + delta)
            event.accept()
            return
        if self._dup_active and self._dup_origin is not None:
            delta = event.scenePos() - self._dup_origin
            for it, start in zip(
                self._dup_items, self._dup_start_positions
            ):
                it.setPos(start + delta)
            event.accept()
            return
        if (
            self._draft_item is not None
            and self._draft_origin is not None
            and self._page_item is not None
        ):
            constrained = bool(event.modifiers() & Qt.ShiftModifier)
            self._update_draft(event.scenePos(), constrained)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._poly_draft is not None:
            # Vertices are placed on press; swallow the release so the
            # base class does not start a selection / move on the draft.
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return

        if self._resize_item is not None:
            self._flush_resize()
            event.accept()
            return

        if self._dup_pending_item is not None:
            # Ctrl+click without a drag: toggle the selection.
            item = self._dup_pending_item
            self._clear_dup_pending()
            item.setSelected(not item.isSelected())
            event.accept()
            return

        if self._dup_active:
            self._finish_duplicate_drag()
            event.accept()
            return

        if self._draft_item is not None:
            self._finish_draft()
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self._flush_pending_moves()

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        if self._poly_draft is not None:
            self.finish_poly_draft()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # multi-click drafting (polyline / polygon)
    # ------------------------------------------------------------------
    _POLY_CLOSE_THRESHOLD = 8.0  # scene px to snap-close a polygon

    def poly_draft_active(self) -> bool:
        return self._poly_draft is not None

    def _discard_poly_draft(self) -> None:
        """Abandon any in-progress multi-click draft (no undo entry)."""
        if self._poly_draft is not None:
            if self._poly_draft.scene() is not None:
                self.removeItem(self._poly_draft)
            self._poly_draft = None
            self._poly_points = []

    def _poly_click(self, tool: Tool, pos: QPointF) -> None:
        if self._page_item is None:
            return
        if self._poly_draft is None:
            item: AnnotationItem = (
                PolylineItem([pos, pos])
                if tool is Tool.POLYLINE
                else PolygonItem([pos, pos])
            )
            item.setParentItem(self._page_item)
            self._apply_current_style(item)
            self._poly_draft = item
            self._poly_points = [QPointF(pos)]
            return
        # Click near the first vertex closes a polygon.
        if (
            isinstance(self._poly_draft, PolygonItem)
            and len(self._poly_points) >= 3
            and (pos - self._poly_points[0]).manhattanLength()
            <= self._POLY_CLOSE_THRESHOLD
        ):
            self.finish_poly_draft()
            return
        self._poly_points.append(QPointF(pos))
        self._poly_draft.set_points(self._poly_points + [QPointF(pos)])

    def finish_poly_draft(self) -> None:
        item = self._poly_draft
        pts = list(self._poly_points)
        self._poly_draft = None
        self._poly_points = []
        if item is None:
            return
        if item.scene() is not None:
            self.removeItem(item)
        # Drop consecutive near-duplicate vertices (e.g. the floating
        # point coinciding with the last committed click).
        cleaned: list[QPointF] = []
        for p in pts:
            if not cleaned or (p - cleaned[-1]).manhattanLength() > 0.5:
                cleaned.append(p)
        min_vertices = 3 if isinstance(item, PolygonItem) else 2
        if len(cleaned) < min_vertices:
            return  # not meaningful: drop without an undo entry
        item.set_points(cleaned)
        self._push_add(item)

    # ------------------------------------------------------------------
    # drafting
    # ------------------------------------------------------------------
    def _make_draft_item(
        self, tool: Tool, pos: QPointF
    ) -> AnnotationItem | None:
        rect = QRectF(pos, pos)
        if tool is Tool.RECTANGLE:
            return RectangleItem(rect)
        if tool is Tool.ELLIPSE:
            return EllipseItem(rect)
        if tool is Tool.CLOUD:
            return CloudItem(rect)
        if tool is Tool.LINE:
            return LineItem(pos, pos)
        if tool is Tool.ARROW:
            return ArrowItem(pos, pos)
        if tool is Tool.FREEHAND:
            return FreehandItem([pos])
        if tool is Tool.CALLOUT:
            return CalloutItem(pos)
        return None

    def _apply_current_style(self, item: AnnotationItem) -> None:
        if self._tool_controller is None:
            return
        item.set_color(self._tool_controller.color())
        item.set_stroke(self._tool_controller.stroke())

    def _update_draft(self, pos: QPointF, constrained: bool = False) -> None:
        assert self._draft_item is not None
        assert self._draft_origin is not None
        item = self._draft_item
        origin = self._draft_origin
        if isinstance(item, (RectangleItem, EllipseItem, CloudItem)):
            if constrained:
                pos = self._square_from(origin, pos)
            item.set_rect(QRectF(origin, pos).normalized())
        elif isinstance(item, (LineItem, ArrowItem)):
            if constrained:
                pos = self._snap_angle(origin, pos, step_deg=45.0)
            item.set_line_points(origin, pos)
        elif isinstance(item, CalloutItem):
            # Drag defines the leader: press = arrow tip (the feature),
            # cursor = text-box anchor. Keep the tip pinned at `origin`.
            item.setPos(pos)
            item.set_tip(QPointF(origin.x() - pos.x(), origin.y() - pos.y()))
        elif isinstance(item, FreehandItem):
            item.add_point(pos)

    @staticmethod
    def _square_from(origin: QPointF, pos: QPointF) -> QPointF:
        """Project `pos` so the rect from `origin` to it is a square.

        Picks the larger of |dx|, |dy| as the side length, preserving the
        sign of each component so the cursor stays on a corner of the
        resulting square.
        """
        dx = pos.x() - origin.x()
        dy = pos.y() - origin.y()
        side = max(abs(dx), abs(dy))
        sx = 1.0 if dx >= 0 else -1.0
        sy = 1.0 if dy >= 0 else -1.0
        return QPointF(origin.x() + sx * side, origin.y() + sy * side)

    @staticmethod
    def _snap_angle(
        origin: QPointF, pos: QPointF, step_deg: float = 45.0
    ) -> QPointF:
        """Snap the angle from `origin` to `pos` to a multiple of `step_deg`.

        The distance is preserved; only the angle is rounded. Mirrors
        PowerPoint's Shift-while-drawing behavior for lines and arrows.
        """
        dx = pos.x() - origin.x()
        dy = pos.y() - origin.y()
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return QPointF(pos)
        step = math.radians(step_deg)
        angle = math.atan2(dy, dx)
        snapped = round(angle / step) * step
        return QPointF(
            origin.x() + length * math.cos(snapped),
            origin.y() + length * math.sin(snapped),
        )

    def _finish_draft(self) -> None:
        item = self._draft_item
        self._draft_item = None
        self._draft_origin = None
        if item is None or self._page_item is None:
            return

        if isinstance(item, CalloutItem):
            # Like text: stay parented and edit inline; the add command
            # is pushed (or rolled back) when editing finishes.
            self._finish_callout_draft(item)
            return

        # Reject empty drafts (no drag).
        if not self._draft_is_meaningful(item):
            self.removeItem(item)
            return

        # Detach from parent so the AddAnnotationCommand owns the placement.
        self.removeItem(item)
        self._push_add(item)

    def _draft_is_meaningful(self, item: AnnotationItem) -> bool:
        if isinstance(item, (RectangleItem, EllipseItem, CloudItem)):
            r = item.rect()
            return r.width() > 1.0 and r.height() > 1.0
        if isinstance(item, (LineItem, ArrowItem)):
            p1, p2 = item.line_points()
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            return (dx * dx + dy * dy) > 1.0
        if isinstance(item, FreehandItem):
            return len(item.points()) > 1
        return True

    # ------------------------------------------------------------------
    # text spawning (no command pushed until edit is confirmed)
    # ------------------------------------------------------------------
    def _spawn_text_at(self, pos: QPointF) -> None:
        if self._page_item is None:
            return
        item = TextAnnotationItem(pos)
        self._apply_current_style(item)
        item.setParentItem(self._page_item)
        item.editingFinished.connect(
            lambda txt, it=item: self._on_text_edit_finished(it, txt)
        )
        item.begin_edit()

    def _finish_callout_draft(self, item: CalloutItem) -> None:
        # A click without a real drag leaves the tip at its default
        # offset (set in the ctor), so the leader is always visible.
        item.editingFinished.connect(
            lambda txt, it=item: self._on_text_edit_finished(it, txt)
        )
        item.begin_edit()

    def _on_text_edit_finished(
        self, item: TextAnnotationItem, text: str
    ) -> None:
        if not text.strip():
            # Empty text -> rollback: just remove without an undo entry.
            if item.scene() is not None:
                self.removeItem(item)
            self.annotationsChanged.emit()
            return
        # Detach and push as a normal AddAnnotationCommand.
        if item.scene() is not None:
            self.removeItem(item)
        self._push_add(item)

    # ------------------------------------------------------------------
    # add / move plumbing
    # ------------------------------------------------------------------
    def push_add(self, item: AnnotationItem) -> None:
        """Public entry: add an externally built item via the undo stack.

        Used by MainWindow for items whose construction needs a modal
        dialog (GD&T) -- the scene cannot drive that flow alone.
        """
        self._push_add(item)

    def _push_add(self, item: AnnotationItem) -> None:
        if self._page_item is None:
            return
        cmd = AddAnnotationCommand(self, self._page_item, item)
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()
        self.annotationsChanged.emit()
        # PowerPoint-style affordance: after every successful insertion,
        # return to the Select tool so the user can immediately reposition
        # or restyle the new annotation. The new item is also selected so
        # the Properties dock targets it on first click.
        self._return_to_select(select_item=item)

    def _return_to_select(
        self, select_item: AnnotationItem | None = None
    ) -> None:
        if self._tool_controller is not None:
            self._tool_controller.set_tool(Tool.SELECT)
        if select_item is not None and select_item.scene() is not None:
            for it in self.selectedItems():
                if it is not select_item:
                    it.setSelected(False)
            select_item.setSelected(True)

    _CORNER_HANDLE_ROLES = (
        HandleRole.TOP_LEFT,
        HandleRole.TOP_RIGHT,
        HandleRole.BOTTOM_LEFT,
        HandleRole.BOTTOM_RIGHT,
    )

    def _constrain_resize(
        self, item: AnnotationItem, role: HandleRole, local_pos: QPointF
    ) -> QPointF:
        """Shift-constrain an in-progress resize on an existing item.

        Mirrors the Shift behavior already used while drafting new shapes:
        a line/arrow endpoint snaps to a 45-degree step from the other
        endpoint (keeps the segment's alignment constant while
        lengthening/rotating it); a rectangle/ellipse/cloud dragged from a
        corner keeps a square footprint.
        """
        if isinstance(item, LineItem) and role in (
            HandleRole.P1,
            HandleRole.P2,
        ):
            p1, p2 = item.line_points()
            anchor = p2 if role is HandleRole.P1 else p1
            return self._snap_angle(anchor, local_pos, step_deg=45.0)
        if (
            isinstance(item, (RectangleItem, EllipseItem, CloudItem))
            and role in self._CORNER_HANDLE_ROLES
        ):
            r = item.rect()
            anchor = {
                HandleRole.TOP_LEFT: QPointF(r.right(), r.bottom()),
                HandleRole.TOP_RIGHT: QPointF(r.left(), r.bottom()),
                HandleRole.BOTTOM_LEFT: QPointF(r.right(), r.top()),
                HandleRole.BOTTOM_RIGHT: QPointF(r.left(), r.top()),
            }[role]
            return self._square_from(anchor, local_pos)
        return local_pos

    def _hit_resize_handle(
        self, scene_pos: QPointF
    ) -> tuple[AnnotationItem, HandleRole] | None:
        """Return (item, role) if `scene_pos` is on a handle of a selected item."""
        # Search topmost-first; selectedItems order is undefined so we
        # rely on items(scene_pos) Z-order.
        selected = {
            it for it in self.selectedItems() if isinstance(it, AnnotationItem)
        }
        if not selected:
            return None
        for it in self.items(scene_pos):
            if it in selected:
                local = it.mapFromScene(scene_pos)
                role = it.hit_handle(local)
                if role is not None:
                    return it, role
        # Fallback: handles on a selected item may stick out beyond the
        # item's bounding rect (corner handles include 4px of padding).
        # Check each selected item explicitly.
        for it in selected:
            local = it.mapFromScene(scene_pos)
            role = it.hit_handle(local)
            if role is not None:
                return it, role
        return None

    def _flush_resize(self) -> None:
        item = self._resize_item
        old = self._resize_snapshot
        self._resize_item = None
        self._resize_role = None
        self._resize_snapshot = None
        if item is None:
            return
        new = item.geom_snapshot()
        if old == new:
            return
        cmd = ResizeCommand(item, old, new)
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()

    def _capture_move_origins(self) -> None:
        self._move_origins = {
            it: QPointF(it.pos())
            for it in self.selectedItems()
            if isinstance(it, AnnotationItem)
        }

    # ------------------------------------------------------------------
    # Ctrl+drag duplicate
    # ------------------------------------------------------------------
    def _topmost_annotation_at(
        self, scene_pos: QPointF
    ) -> AnnotationItem | None:
        for it in self.items(scene_pos):
            if isinstance(it, AnnotationItem):
                return it
        return None

    def _clear_dup_pending(self) -> None:
        self._dup_pending_item = None
        self._dup_pending_scene = None
        self._dup_pending_screen = None

    def _begin_duplicate_drag(
        self, clicked: AnnotationItem, origin: QPointF
    ) -> None:
        """Clone the relevant items and start a custom drag on the copies.

        Matches PowerPoint: if the clicked item is part of the current
        selection, duplicate all selected items; otherwise duplicate
        only the clicked one. The clones become the active selection,
        the originals stay put.
        """
        if self._page_item is None:
            return
        selected = [
            it
            for it in self.selectedItems()
            if isinstance(it, AnnotationItem)
        ]
        if clicked in selected:
            sources = selected
        else:
            sources = [clicked]

        clones: list[AnnotationItem] = []
        for src in sources:
            try:
                clones.append(src.clone())
            except NotImplementedError:
                # Defensive: skip uncloneable items rather than abort.
                continue
        if not clones:
            return

        # Wrap clone-adds in a macro so undo collapses the whole gesture.
        stack = self._undo_stack
        if stack is not None:
            stack.beginMacro("Duplicate annotation(s)")
        for c in clones:
            cmd = AddAnnotationCommand(self, self._page_item, c)
            if stack is not None:
                stack.push(cmd)
            else:
                cmd.redo()

        # Reset selection to the clones so the drag (and Properties dock)
        # tracks them rather than the originals.
        for it in self.selectedItems():
            it.setSelected(False)
        for c in clones:
            c.setSelected(True)

        self._dup_active = True
        self._dup_origin = QPointF(origin)
        self._dup_items = clones
        self._dup_start_positions = [QPointF(c.pos()) for c in clones]
        self.annotationsChanged.emit()

    def _finish_duplicate_drag(self) -> None:
        items = list(self._dup_items)
        starts = list(self._dup_start_positions)
        self._dup_active = False
        self._dup_origin = None
        self._dup_items = []
        self._dup_start_positions = []

        moves: list[tuple[AnnotationItem, QPointF, QPointF]] = []
        for it, start in zip(items, starts):
            end = QPointF(it.pos())
            if (end - start).manhattanLength() > 0.0:
                moves.append((it, start, end))
        stack = self._undo_stack
        if moves:
            cmd = MoveAnnotationsCommand(moves)
            if stack is not None:
                stack.push(cmd)
            else:
                cmd.redo()
        if stack is not None:
            stack.endMacro()

    def _flush_pending_moves(self) -> None:
        if not self._move_origins:
            return
        moves: list[tuple[AnnotationItem, QPointF, QPointF]] = []
        for item, old in self._move_origins.items():
            new = item.pos()
            if (new - old).manhattanLength() > 0.0:
                moves.append((item, old, QPointF(new)))
        self._move_origins.clear()
        if not moves:
            return
        cmd = MoveAnnotationsCommand(moves)
        if self._undo_stack is not None:
            self._undo_stack.push(cmd)
        else:
            cmd.redo()
