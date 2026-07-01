"""PdfScene: holds the current page's pixmap and its annotation children.

One page at a time (no continuous scroll). Repopulated on page switch.
The scene also dispatches mouse events to the active tool: drawing
tools build a new annotation; the SELECT tool falls back to the default
QGraphicsScene behavior (selection / move).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
)
from PySide6.QtGui import QUndoStack

from annoter.controllers.commands import (
    AddAnnotationCommand,
    MoveAnnotationsCommand,
    ResizeCommand,
)
from annoter.controllers.geometry import item_local_rect, item_scene_rect
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
    formatPaintRequested = Signal(object)  # AnnotationItem clicked while painting

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

        # Smart alignment guides (PowerPoint/Canva-style): only armed
        # during a real, single-item mouse drag (see mousePressEvent) so
        # programmatic moves (undo/redo, Align commands, duplicate-drag)
        # are never perturbed by itemChange's snap hook.
        self._interactive_drag_active: bool = False
        self._guide_v: QGraphicsLineItem | None = None
        self._guide_h: QGraphicsLineItem | None = None

        # Session-level grouping (Ctrl+G): each group is a set of items
        # that select and move together. Not a QGraphicsItemGroup --
        # annotations stay direct children of the page item, which is
        # what persistence, alignment and snapping all assume -- so this
        # is intentionally not persisted across save/reopen (documented
        # limitation, see PLAN.md).
        self._groups: list[set[AnnotationItem]] = []
        # Unified dashed outline drawn around a fully-selected group
        # (PowerPoint-style "this is one object"), kept in sync via
        # Qt's native selectionChanged signal and refreshed after every
        # drag tick (see mouseMoveEvent).
        self._group_box: QGraphicsRectItem | None = None
        self.selectionChanged.connect(self._update_group_box)

        # Manual group-drag: clicking empty space *inside* a group's
        # bounding box (not on any specific member) still grabs and
        # moves the whole group, like PowerPoint. Mirrors the Ctrl-drag
        # duplicate machinery but without cloning.
        self._group_drag_active: bool = False
        self._group_drag_origin: QPointF | None = None
        self._group_drag_items: list[AnnotationItem] = []
        self._group_drag_start_positions: list[QPointF] = []

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
        self._interactive_drag_active = False
        self._guide_v = None
        self._guide_h = None
        self._groups = []
        self._group_box = None
        self._group_drag_active = False
        self._group_drag_origin = None
        self._group_drag_items = []
        self._group_drag_start_positions = []

    # ------------------------------------------------------------------
    # session-level grouping
    # ------------------------------------------------------------------
    def group_of(self, item: AnnotationItem) -> set[AnnotationItem] | None:
        for g in self._groups:
            if item in g:
                return g
        return None

    def group_selection(self) -> bool:
        """Group the selected annotations (need >= 2). Items already in
        another group are moved into the new one."""
        items = [
            it for it in self.selectedItems() if isinstance(it, AnnotationItem)
        ]
        if len(items) < 2:
            return False
        items_set = set(items)
        self._groups = [g - items_set for g in self._groups]
        self._groups = [g for g in self._groups if len(g) >= 2]
        self._groups.append(items_set)
        return True

    def ungroup_selection(self) -> bool:
        """Dissolve every group touched by the current selection."""
        items = {
            it for it in self.selectedItems() if isinstance(it, AnnotationItem)
        }
        before = len(self._groups)
        self._groups = [g for g in self._groups if g.isdisjoint(items)]
        return len(self._groups) != before

    def has_group_in_selection(self) -> bool:
        items = {
            it for it in self.selectedItems() if isinstance(it, AnnotationItem)
        }
        return any(not g.isdisjoint(items) for g in self._groups)

    def _select_group(self, group: set[AnnotationItem]) -> None:
        for it in list(self.selectedItems()):
            if it not in group:
                it.setSelected(False)
        for it in group:
            it.setSelected(True)

    def _group_union_rect(self, group: set[AnnotationItem]) -> QRectF | None:
        union = None
        for it in group:
            r = item_scene_rect(it)
            union = r if union is None else union.united(r)
        return union

    def _group_at_point(self, scene_pos: QPointF) -> set[AnnotationItem] | None:
        """The group (if any) whose combined bounding box contains
        `scene_pos`, so a click on empty space *inside* a group's
        silhouette still grabs it, like PowerPoint."""
        for g in self._groups:
            union = self._group_union_rect(g)
            if union is not None and union.contains(scene_pos):
                return g
        return None

    def _begin_group_drag(self, origin: QPointF) -> None:
        items = [
            it for it in self.selectedItems() if isinstance(it, AnnotationItem)
        ]
        self._group_drag_active = True
        self._group_drag_origin = QPointF(origin)
        self._group_drag_items = items
        self._group_drag_start_positions = [QPointF(it.pos()) for it in items]
        self._capture_move_origins()
        self._interactive_drag_active = True

    def _finish_group_drag(self) -> None:
        items = list(self._group_drag_items)
        starts = list(self._group_drag_start_positions)
        self._group_drag_active = False
        self._group_drag_origin = None
        self._group_drag_items = []
        self._group_drag_start_positions = []
        self._interactive_drag_active = False
        self._hide_guides()

        moves: list[tuple[AnnotationItem, QPointF, QPointF]] = []
        for it, start in zip(items, starts):
            end = QPointF(it.pos())
            if (end - start).manhattanLength() > 0.0:
                moves.append((it, start, end))
        if moves:
            cmd = MoveAnnotationsCommand(moves)
            if self._undo_stack is not None:
                self._undo_stack.push(cmd)
            else:
                cmd.redo()

    def _update_group_box(self) -> None:
        """Draw a single dashed outline around a fully-selected group,
        replacing the usual per-item selection chrome with a clear "this
        moves as one object" cue. Hidden for any other selection."""
        selected = {
            it for it in self.selectedItems() if isinstance(it, AnnotationItem)
        }
        matching = next((g for g in self._groups if g == selected), None)
        if matching is None or self._page_item is None:
            if self._group_box is not None:
                self._group_box.hide()
            return
        union = self._group_union_rect(matching)
        if union is None:
            return
        if self._group_box is None:
            box = QGraphicsRectItem(self._page_item)
            pen = QPen(QColor("#1E88E5"), 0)
            pen.setStyle(Qt.DashLine)
            pen.setCosmetic(True)
            box.setPen(pen)
            box.setBrush(Qt.NoBrush)
            box.setZValue(999_999.0)
            box.setAcceptedMouseButtons(Qt.NoButton)
            self._group_box = box
        margin = 4.0
        self._group_box.setRect(union.adjusted(-margin, -margin, margin, margin))
        self._group_box.show()

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
    # live measurement (used by the view's measurement HUD)
    # ------------------------------------------------------------------
    def current_measurement(self) -> tuple[str, float, float] | None:
        """Geometry of the in-progress draft or handle-resize, if any.

        Returns `("rect", width_px, height_px)` for rect-like shapes or
        `("line", length_px, angle_deg)` for a line/arrow; `None` when
        nothing is being drafted/resized or the item's shape doesn't fit
        either description (GD&T frames, text, freehand, polylines...).
        """
        item = (
            self._draft_item
            if self._draft_item is not None
            else self._resize_item
        )
        if item is None:
            return None
        if isinstance(
            item, (RectangleItem, EllipseItem, CloudItem, PolygonItem)
        ):
            r = item.rect()
            return ("rect", r.width(), r.height())
        if isinstance(item, LineItem):
            p1, p2 = item.line_points()
            dx = p2.x() - p1.x()
            dy = p2.y() - p1.y()
            length = math.hypot(dx, dy)
            angle = math.degrees(math.atan2(dy, dx))
            return ("line", length, angle)
        return None

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
            # A plain click on a grouped item selects every member, so
            # the drag Qt is about to start moves the whole group.
            clicked = self._topmost_annotation_at(event.scenePos())
            if clicked is not None:
                group = self.group_of(clicked)
                if group is not None:
                    self._select_group(group)
                super().mousePressEvent(event)
                self._capture_move_origins()
                self._interactive_drag_active = True
                return
            # PowerPoint lets you grab a group anywhere inside its
            # silhouette, not just on a member shape: a click on empty
            # space that still falls within a group's combined bounding
            # box selects and drags the whole group. Handled manually
            # (mirroring Ctrl-drag duplicate) since no item is under the
            # cursor for Qt's native drag to grab.
            hit_group = self._group_at_point(event.scenePos())
            if hit_group is not None:
                self._select_group(hit_group)
                self._begin_group_drag(event.scenePos())
                event.accept()
                return
            super().mousePressEvent(event)
            self._capture_move_origins()
            self._interactive_drag_active = True
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

        if tool is Tool.FORMAT_PAINTER:
            # Defer to MainWindow: it holds the captured source style and
            # pushes a ChangePropsCommand for the clicked target.
            clicked = self._topmost_annotation_at(pos)
            if clicked is not None:
                self.formatPaintRequested.emit(clicked)
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
            snapped_scene = None
            if (
                not (event.modifiers() & Qt.AltModifier)
                and isinstance(self._resize_item, LineItem)
                and self._resize_role in (HandleRole.P1, HandleRole.P2)
            ):
                snapped_scene = self._nearest_shape_snap_point(
                    event.scenePos(), exclude=self._resize_item
                )
            if snapped_scene is not None:
                local = self._resize_item.mapFromScene(snapped_scene)
            elif event.modifiers() & Qt.ShiftModifier:
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
        if self._group_drag_active and self._group_drag_origin is not None:
            delta = event.scenePos() - self._group_drag_origin
            for it, start in zip(
                self._group_drag_items, self._group_drag_start_positions
            ):
                # setPos still runs through itemChange, so axis-lock and
                # (single-item-only) guide-snapping apply consistently
                # whether the drag is native or this manual fallback.
                it.setPos(start + delta)
            self._update_group_box()
            event.accept()
            return
        if (
            self._draft_item is not None
            and self._draft_origin is not None
            and self._page_item is not None
        ):
            constrained = bool(event.modifiers() & Qt.ShiftModifier)
            snap_disabled = bool(event.modifiers() & Qt.AltModifier)
            self._update_draft(event.scenePos(), constrained, snap_disabled)
            event.accept()
            return
        super().mouseMoveEvent(event)
        self._update_group_box()

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

        if self._group_drag_active:
            self._finish_group_drag()
            event.accept()
            return

        if self._draft_item is not None:
            self._finish_draft()
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self._flush_pending_moves()
        self._interactive_drag_active = False
        self._hide_guides()

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

    def _update_draft(
        self,
        pos: QPointF,
        constrained: bool = False,
        snap_disabled: bool = False,
    ) -> None:
        assert self._draft_item is not None
        assert self._draft_origin is not None
        item = self._draft_item
        origin = self._draft_origin
        if isinstance(item, (RectangleItem, EllipseItem, CloudItem)):
            if constrained:
                pos = self._square_from(origin, pos)
            item.set_rect(QRectF(origin, pos).normalized())
        elif isinstance(item, (LineItem, ArrowItem)):
            snapped = (
                None
                if snap_disabled
                else self._nearest_shape_snap_point(pos, exclude=item)
            )
            if snapped is not None:
                pos = snapped
            elif constrained:
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

    # ------------------------------------------------------------------
    # smart alignment guides (PowerPoint/Canva-style snap-while-dragging)
    # ------------------------------------------------------------------
    _SNAP_THRESHOLD_PX = 6.0

    def maybe_snap_move(
        self, item: AnnotationItem, proposed_pos: QPointF
    ) -> QPointF:
        """Called from `AnnotationItem.itemChange` on every proposed
        position during a drag. Snaps to the nearest aligned edge/center
        of another annotation or the page, within `_SNAP_THRESHOLD_PX`,
        and shows a dashed guide line at the snapped coordinate.

        Only active for a real mouse drag (see `_interactive_drag_active`);
        a no-op otherwise so programmatic moves (undo/redo, Align
        commands, duplicate-drag) are never second-guessed. Two
        modifiers change the behavior, mutually exclusively:
          - Alt: disable snapping entirely for this drag (temporary
            override, like PowerPoint/Figma).
          - Shift: axis-lock to whichever of X/Y has moved further from
            the drag's start (PowerPoint's constrained-drag). Applies to
            every selected item (including a group), unlike plain
            guide-snapping, which is scoped to a single selected item --
            snapping each item of a multi-selection independently could
            pull the group's relative layout apart, but locking them all
            to the same axis cannot.
        """
        if not self._interactive_drag_active or self._page_item is None:
            return proposed_pos

        mods = QApplication.keyboardModifiers()
        if mods & Qt.AltModifier:
            self._hide_guides()
            return proposed_pos
        if mods & Qt.ShiftModifier:
            self._hide_guides()
            return self._apply_axis_lock(item, proposed_pos)
        if len(self.selectedItems()) != 1:
            return proposed_pos

        local = item_local_rect(item)
        rect = local.translated(proposed_pos)
        page_rect = self._page_item.boundingRect()

        x_targets = [page_rect.left(), page_rect.center().x(), page_rect.right()]
        y_targets = [page_rect.top(), page_rect.center().y(), page_rect.bottom()]
        for sibling in self._page_item.childItems():
            if sibling is item or not isinstance(sibling, AnnotationItem):
                continue
            sr = item_scene_rect(sibling)
            x_targets.extend([sr.left(), sr.center().x(), sr.right()])
            y_targets.extend([sr.top(), sr.center().y(), sr.bottom()])

        snap_x, guide_x = self._best_snap(
            [rect.left(), rect.center().x(), rect.right()], x_targets
        )
        snap_y, guide_y = self._best_snap(
            [rect.top(), rect.center().y(), rect.bottom()], y_targets
        )

        result = QPointF(proposed_pos)
        if snap_x is not None:
            result.setX(proposed_pos.x() + snap_x)
            self._show_guide_v(guide_x, page_rect)
        else:
            self._hide_guide_v()
        if snap_y is not None:
            result.setY(proposed_pos.y() + snap_y)
            self._show_guide_h(guide_y, page_rect)
        else:
            self._hide_guide_h()
        return result

    def _apply_axis_lock(
        self, item: AnnotationItem, proposed_pos: QPointF
    ) -> QPointF:
        """Constrain `item` to move only horizontally or only vertically
        from its drag-start position (`_move_origins`), whichever axis
        has the larger cumulative displacement so far -- re-evaluated on
        every move, so the software follows the direction the user is
        actually dragging in, PowerPoint-style. A no-op if the item's
        drag-start position wasn't captured (e.g. not part of the
        current drag)."""
        origin = self._move_origins.get(item)
        if origin is None:
            return proposed_pos
        dx = proposed_pos.x() - origin.x()
        dy = proposed_pos.y() - origin.y()
        if abs(dx) >= abs(dy):
            return QPointF(proposed_pos.x(), origin.y())
        return QPointF(origin.x(), proposed_pos.y())

    def _best_snap(
        self, moving_candidates: list[float], targets: list[float]
    ) -> tuple[float | None, float]:
        """Smallest-delta (moving -> target) within threshold, or
        (None, 0.0). Returns (delta, target) so the caller can both
        adjust the position and place the guide line."""
        best_delta: float | None = None
        best_target = 0.0
        best_abs = self._SNAP_THRESHOLD_PX
        for m in moving_candidates:
            for t in targets:
                d = t - m
                if abs(d) <= best_abs:
                    best_abs = abs(d)
                    best_delta = d
                    best_target = t
        return best_delta, best_target

    def _show_guide_v(self, x: float, page_rect: QRectF) -> None:
        if self._guide_v is None:
            self._guide_v = self._make_guide_item()
        self._guide_v.setLine(x, page_rect.top(), x, page_rect.bottom())
        self._guide_v.show()

    def _show_guide_h(self, y: float, page_rect: QRectF) -> None:
        if self._guide_h is None:
            self._guide_h = self._make_guide_item()
        self._guide_h.setLine(page_rect.left(), y, page_rect.right(), y)
        self._guide_h.show()

    def _hide_guide_v(self) -> None:
        if self._guide_v is not None:
            self._guide_v.hide()

    def _hide_guide_h(self) -> None:
        if self._guide_h is not None:
            self._guide_h.hide()

    def _hide_guides(self) -> None:
        self._hide_guide_v()
        self._hide_guide_h()

    def _make_guide_item(self) -> QGraphicsLineItem:
        line = QGraphicsLineItem(self._page_item)
        pen = QPen(QColor("#E91E63"), 0)  # cosmetic (device-pixel width)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        line.setPen(pen)
        line.setZValue(1_000_000.0)  # always above annotations
        line.setAcceptedMouseButtons(Qt.NoButton)
        return line

    # ------------------------------------------------------------------
    # line/arrow endpoint snapping (PowerPoint connector-style)
    # ------------------------------------------------------------------
    _ENDPOINT_SNAP_THRESHOLD_PX = 8.0

    def _nearest_shape_snap_point(
        self, scene_pos: QPointF, exclude: AnnotationItem
    ) -> QPointF | None:
        """Nearest "connection point" (corner / edge-midpoint / center of
        another annotation's bounding rect) within the snap threshold, or
        None. Used while drafting or resizing a line/arrow so its
        endpoint can attach precisely to a nearby shape, the way
        PowerPoint's connectors snap to a shape's connection points.
        """
        if self._page_item is None:
            return None
        best_point: QPointF | None = None
        best_dist = self._ENDPOINT_SNAP_THRESHOLD_PX
        for sibling in self._page_item.childItems():
            if sibling is exclude or not isinstance(sibling, AnnotationItem):
                continue
            r = item_scene_rect(sibling)
            cx, cy = r.center().x(), r.center().y()
            candidates = (
                QPointF(r.left(), r.top()),
                QPointF(cx, r.top()),
                QPointF(r.right(), r.top()),
                QPointF(r.left(), cy),
                QPointF(cx, cy),
                QPointF(r.right(), cy),
                QPointF(r.left(), r.bottom()),
                QPointF(cx, r.bottom()),
                QPointF(r.right(), r.bottom()),
            )
            for c in candidates:
                d = math.hypot(c.x() - scene_pos.x(), c.y() - scene_pos.y())
                if d <= best_dist:
                    best_dist = d
                    best_point = c
        return best_point

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
