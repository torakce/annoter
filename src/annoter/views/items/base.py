"""AnnotationItem: common base for every annotation graphics item.

Carries color, stroke width, selection/hover behavior, and the hooks
needed by undo commands and the persistence layer.

All annotation items are children of the page's `QGraphicsPixmapItem`
so their coordinates are page-local.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem

from annoter.model.styles import DASH_PATTERNS, DashStyle, HandleRole


# Visual size of a resize handle, in scene units (pixels at zoom 1).
HANDLE_HALF: float = 4.0
HANDLE_HIT_HALF: float = 6.0  # forgiving hit area, slightly larger than visual

_CORNER_HANDLES = {
    HandleRole.TOP_LEFT,
    HandleRole.TOP_RIGHT,
    HandleRole.BOTTOM_LEFT,
    HandleRole.BOTTOM_RIGHT,
}

_CURSOR_BY_ROLE = {
    HandleRole.TOP_LEFT: Qt.SizeFDiagCursor,
    HandleRole.BOTTOM_RIGHT: Qt.SizeFDiagCursor,
    HandleRole.TOP_RIGHT: Qt.SizeBDiagCursor,
    HandleRole.BOTTOM_LEFT: Qt.SizeBDiagCursor,
    HandleRole.TOP: Qt.SizeVerCursor,
    HandleRole.BOTTOM: Qt.SizeVerCursor,
    HandleRole.LEFT: Qt.SizeHorCursor,
    HandleRole.RIGHT: Qt.SizeHorCursor,
    HandleRole.P1: Qt.SizeAllCursor,
    HandleRole.P2: Qt.SizeAllCursor,
}


class AnnotationItem(QGraphicsItem):
    """Common base. Subclasses set KIND and implement boundingRect/paint."""

    KIND: str = ""

    def __init__(self, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        self._color: QColor = QColor("#E53935")
        self._stroke: float = 2.0
        self._dash_style: DashStyle = DashStyle.SOLID

    # ------------------------------------------------------------------
    # style
    # ------------------------------------------------------------------
    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor) -> None:
        c = QColor(color)
        if c == self._color:
            return
        self._color = c
        self.update()

    def stroke(self) -> float:
        return self._stroke

    def set_stroke(self, width: float) -> None:
        w = float(width)
        if w == self._stroke:
            return
        self.prepareGeometryChange()
        self._stroke = w
        self.update()

    def dash_style(self) -> DashStyle:
        return self._dash_style

    def set_dash_style(self, style: DashStyle) -> None:
        if style is self._dash_style:
            return
        self._dash_style = style
        self.update()

    def _apply_dash(self, pen: QPen) -> QPen:
        pattern = DASH_PATTERNS.get(self._dash_style, [])
        if not pattern:
            pen.setStyle(Qt.SolidLine)
        else:
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern(pattern)
        return pen

    # ------------------------------------------------------------------
    # display
    # ------------------------------------------------------------------
    def label(self) -> str:
        """Short label shown in the annotation list dock."""
        return self.KIND.capitalize() if self.KIND else "Annotation"

    def _selection_pen(self) -> QPen:
        pen = QPen(QColor(0, 0, 0, 160), 0, Qt.DashLine)
        pen.setCosmetic(True)
        return pen

    def _draw_selection_marker(self, painter, rect: QRectF) -> None:
        if not self.isSelected():
            return
        painter.save()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(self._selection_pen())
        painter.drawRect(rect)
        painter.restore()
        self._draw_handles(painter)

    # ------------------------------------------------------------------
    # resize handles
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict[HandleRole, QPointF]:
        """Local-coordinate position of each resize handle.

        Default: no handles. Subclasses opt in by returning a non-empty
        mapping; resize gestures will then dispatch through
        `apply_resize` / `geom_snapshot` / `apply_geom`.
        """
        return {}

    def apply_resize(self, role: HandleRole, local_pos: QPointF) -> None:
        """Apply an in-progress resize. Default no-op."""

    def geom_snapshot(self) -> object:
        """Opaque snapshot of geometry, paired with `apply_geom` for undo."""
        return None

    def apply_geom(self, snapshot: object) -> None:
        """Restore a snapshot returned by `geom_snapshot`."""

    def _handle_visual_rect(self, pt: QPointF) -> QRectF:
        return QRectF(
            pt.x() - HANDLE_HALF,
            pt.y() - HANDLE_HALF,
            2 * HANDLE_HALF,
            2 * HANDLE_HALF,
        )

    def _handle_hit_rect(self, pt: QPointF) -> QRectF:
        return QRectF(
            pt.x() - HANDLE_HIT_HALF,
            pt.y() - HANDLE_HIT_HALF,
            2 * HANDLE_HIT_HALF,
            2 * HANDLE_HIT_HALF,
        )

    def hit_handle(self, local_pos: QPointF) -> HandleRole | None:
        positions = self.handle_positions()
        if not positions:
            return None
        # Prefer corner / endpoint handles over edge handles when both
        # contain the click (corner hit areas overlap edge ones).
        ordered = sorted(
            positions.items(),
            key=lambda kv: 0 if kv[0] in _CORNER_HANDLES else 1,
        )
        for role, pt in ordered:
            if self._handle_hit_rect(pt).contains(local_pos):
                return role
        return None

    def _draw_handles(self, painter) -> None:
        positions = self.handle_positions()
        if not positions:
            return
        painter.save()
        pen = QPen(QColor("#1E88E5"), 0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        for pt in positions.values():
            painter.drawRect(self._handle_visual_rect(pt))
        painter.restore()

    def handles_extent(self) -> float:
        """Margin to add to boundingRect so handles aren't clipped or
        missed by the scene's hit testing (the hit area extends a bit
        past the visual square)."""
        if not self.isSelected():
            return 0.0
        if not self.handle_positions():
            return 0.0
        return HANDLE_HIT_HALF + 1.0

    # ------------------------------------------------------------------
    # hover -> contextual cursor when above a handle
    # ------------------------------------------------------------------
    def hoverMoveEvent(self, event) -> None:  # noqa: ANN001
        if self.isSelected():
            role = self.hit_handle(event.pos())
            if role is not None:
                self.setCursor(_CURSOR_BY_ROLE.get(role, Qt.ArrowCursor))
                super().hoverMoveEvent(event)
                return
        self.unsetCursor()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: ANN001
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):  # noqa: ANN001
        # Selection toggles the handle margin baked into boundingRect.
        # Tell Qt's BSP index so it doesn't keep a stale bounds cache.
        if change == QGraphicsItem.ItemSelectedHasChanged:
            self.prepareGeometryChange()
        elif change == QGraphicsItem.ItemPositionChange:
            # Smart-guide snapping (PowerPoint/Canva-style): let the
            # scene adjust the proposed position during an interactive
            # drag. Delegated (duck-typed) so this base class doesn't
            # need to know about sibling items or page geometry; the
            # scene ignores the request outside of a real mouse drag
            # (e.g. undo/redo, Align commands), so programmatic moves
            # are never perturbed.
            scene = self.scene()
            if scene is not None and hasattr(scene, "maybe_snap_move"):
                return scene.maybe_snap_move(self, QPointF(value))
        return super().itemChange(change, value)

    # ------------------------------------------------------------------
    # duplication
    # ------------------------------------------------------------------
    def _copy_base_style_into(self, dst: "AnnotationItem") -> None:
        """Copy color/stroke/dash + position into another item.

        Subclasses call this from their own `clone()` after building the
        new instance with type-specific geometry.
        """
        dst.set_color(self.color())
        dst.set_stroke(self.stroke())
        dst.set_dash_style(self.dash_style())
        dst.setPos(self.pos())

    def clone(self) -> "AnnotationItem":
        """Return a detached copy of this item.

        Subclasses must override to copy their type-specific state. The
        clone has no parent and is not in any scene; callers wire it up.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement clone()"
        )
