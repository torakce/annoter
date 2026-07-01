"""PdfView: the central QGraphicsView.

Owns zoom, pan, and zoom-window. Mouse-event-to-tool dispatch is added
in M2 (currently the view only handles navigation).

Zoom convention (locked across the codebase):
    factor = 1.0  -> 1 screen pixel per PDF point (real size)
    view_scale (Qt transform) = factor * 72 / render_dpi
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QGraphicsView, QRubberBand

from annoter.config import (
    BASE_RENDER_DPI,
    ZOOM_MAX,
    ZOOM_MIN,
    ZOOM_STEP,
)
from annoter.controllers.geometry import px_to_pt
from annoter.controllers.tools import Tool
from annoter.views.measurement_hud import MeasurementHud


class PdfView(QGraphicsView):
    """QGraphicsView specialized for PDF pages."""

    zoomChanged = Signal(float)  # current zoom factor (1.0 = 100 %)
    # Emitted on right-click: (global QPoint for menu pos, scene QPointF
    # for hit-testing). MainWindow listens and builds the QMenu.
    contextMenuRequested = Signal(QPoint, QPointF)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        # SELECT (the startup tool) drags a rubber-band over empty page
        # areas; presses on an annotation still move it (Qt only starts
        # the band when no item consumes the press). Drawing tools
        # switch to NoDrag -- see set_tool_cursor_for.
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.IntersectsItemShape)
        self.setMouseTracking(True)

        self._zoom: float = 1.0
        self._render_dpi: int = BASE_RENDER_DPI

        self._space_held: bool = False
        self._panning: bool = False
        self._pan_start: QPoint | None = None

        self._zoom_window_armed: bool = False
        self._rubber_band: QRubberBand | None = None
        self._rb_origin: QPoint | None = None

        # Tool-dependent cursor: drawing tools show a crosshair so the
        # user can see exactly where the next click will start.
        self._tool_cursor: Qt.CursorShape = Qt.ArrowCursor

        # Live size/length readout while drafting or resizing a shape.
        self._measurement_hud = MeasurementHud(self.viewport())

    # ------------------------------------------------------------------
    # zoom
    # ------------------------------------------------------------------
    def zoom(self) -> float:
        return self._zoom

    def zoom_in(self) -> None:
        self._apply_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        self._apply_zoom(self._zoom / ZOOM_STEP)

    def zoom_to_actual(self) -> None:
        self._apply_zoom(1.0)

    def zoom_to_fit(self) -> None:
        scene = self.scene()
        if scene is None:
            return
        rect = scene.itemsBoundingRect()
        vp = self.viewport()
        if rect.isEmpty() or vp.width() <= 0 or vp.height() <= 0:
            return
        margin_px = 16
        avail_w = max(1, vp.width() - margin_px)
        avail_h = max(1, vp.height() - margin_px)
        view_scale = min(avail_w / rect.width(), avail_h / rect.height())
        self._apply_zoom(view_scale * self._render_dpi / 72.0)
        self.centerOn(rect.center())

    def arm_zoom_window(self) -> None:
        """Next click+drag selects a rectangle to zoom into."""
        self._zoom_window_armed = True
        self.viewport().setCursor(Qt.CrossCursor)

    def set_tool_cursor_for(self, tool: Tool) -> None:
        """Pick a cursor that reflects the active tool.

        Called by MainWindow on every toolChanged signal so the viewport
        cursor stays in sync with the current drawing mode.
        """
        if tool in (
            Tool.RECTANGLE,
            Tool.ELLIPSE,
            Tool.CLOUD,
            Tool.LINE,
            Tool.ARROW,
            Tool.POLYLINE,
            Tool.POLYGON,
            Tool.CALLOUT,
            Tool.STICKY_NOTE,
            Tool.STAMP,
            Tool.FREEHAND,
            Tool.GDT,
        ):
            cursor = Qt.CrossCursor
        elif tool is Tool.TEXT:
            cursor = Qt.IBeamCursor
        elif tool is Tool.FORMAT_PAINTER:
            cursor = Qt.PointingHandCursor
        else:
            cursor = Qt.ArrowCursor
        self._tool_cursor = cursor
        # Rubber-band selection only makes sense for the Select tool;
        # drawing tools own the drag gesture.
        self.setDragMode(
            QGraphicsView.RubberBandDrag
            if tool is Tool.SELECT
            else QGraphicsView.NoDrag
        )
        # Only apply if no transient cursor (pan, zoom-window) is active.
        if (
            not self._panning
            and not self._zoom_window_armed
            and not self._space_held
        ):
            self.viewport().setCursor(cursor)

    def _apply_zoom(self, factor: float) -> None:
        factor = max(ZOOM_MIN, min(ZOOM_MAX, factor))
        self._zoom = factor
        view_scale = factor * 72.0 / self._render_dpi
        t = QTransform()
        t.scale(view_scale, view_scale)
        self.setTransform(t)
        self.zoomChanged.emit(self._zoom)

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            if not self._panning:
                self.viewport().setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            scene = self.scene()
            if scene is not None and hasattr(scene, "cancel_current_action"):
                scene.cancel_current_action()
                event.accept()
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            scene = self.scene()
            if (
                scene is not None
                and hasattr(scene, "poly_draft_active")
                and scene.poly_draft_active()
            ):
                scene.finish_poly_draft()
                event.accept()
                return
        if event.key() in (
            Qt.Key_Left,
            Qt.Key_Right,
            Qt.Key_Up,
            Qt.Key_Down,
        ):
            scene = self.scene()
            if scene is not None and hasattr(scene, "nudge_selection"):
                step = 10.0 if event.modifiers() & Qt.ShiftModifier else 1.0
                dx, dy = 0.0, 0.0
                if event.key() == Qt.Key_Left:
                    dx = -step
                elif event.key() == Qt.Key_Right:
                    dx = step
                elif event.key() == Qt.Key_Up:
                    dy = -step
                elif event.key() == Qt.Key_Down:
                    dy = step
                # Only consume the event if the scene actually has a
                # selection; otherwise let arrow keys scroll as usual.
                if any(scene.selectedItems()):
                    scene.nudge_selection(dx, dy)
                    event.accept()
                    return

        # F2 or any printable character on a single selected annotation
        # that supports text editing: drop into edit mode (and forward
        # the typed character so the user keeps their typing flow).
        if self._maybe_start_typing(event):
            event.accept()
            return

        super().keyPressEvent(event)

    def _maybe_start_typing(self, event: QKeyEvent) -> bool:
        # Skip when modifiers other than Shift are held -- Ctrl+X / Ctrl+C
        # etc. must reach their shortcuts. We allow Shift because Shift
        # alone is needed to type uppercase letters.
        mods = event.modifiers()
        if mods & (
            Qt.ControlModifier
            | Qt.AltModifier
            | Qt.MetaModifier
        ):
            return False
        scene = self.scene()
        if scene is None:
            return False
        sel = [it for it in scene.selectedItems() if hasattr(it, "start_typing")]
        if len(sel) != 1:
            return False
        target = sel[0]
        if event.key() == Qt.Key_F2:
            target.start_typing("")
            return True
        text = event.text()
        if not text:
            return False
        # Filter out non-printable control chars (Enter/Tab/Backspace).
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
            return False
        target.start_typing(text)
        return True

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning:
                self.viewport().setCursor(self._tool_cursor)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._zoom_window_armed and event.button() == Qt.LeftButton:
            self._rb_origin = event.pos()
            if self._rubber_band is None:
                self._rubber_band = QRubberBand(
                    QRubberBand.Rectangle, self.viewport()
                )
            self._rubber_band.setGeometry(QRect(self._rb_origin, QSize()))
            self._rubber_band.show()
            event.accept()
            return

        space_pan = self._space_held and event.button() == Qt.LeftButton
        middle_pan = event.button() == Qt.MiddleButton
        if space_pan or middle_pan:
            self._panning = True
            self._pan_start = event.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._zoom_window_armed and self._rb_origin is not None:
            assert self._rubber_band is not None
            self._rubber_band.setGeometry(
                QRect(self._rb_origin, event.pos()).normalized()
            )
            event.accept()
            return

        if self._panning and self._pan_start is not None:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - delta.x())
            v.setValue(v.value() - delta.y())
            event.accept()
            return

        super().mouseMoveEvent(event)
        self._update_measurement_hud(event.pos())

    def _update_measurement_hud(self, view_pos: QPoint) -> None:
        scene = self.scene()
        measurement = (
            scene.current_measurement()
            if scene is not None and hasattr(scene, "current_measurement")
            else None
        )
        if measurement is None:
            self._measurement_hud.hide()
            return
        kind, a, b = measurement
        if kind == "rect":
            self._measurement_hud.show_rect(
                px_to_pt(a), px_to_pt(b), view_pos
            )
        elif kind == "line":
            self._measurement_hud.show_line(px_to_pt(a), b, view_pos)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        self._measurement_hud.hide()
        super().leaveEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        scene_pos = self.mapToScene(event.pos())
        self.contextMenuRequested.emit(event.globalPos(), scene_pos)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._zoom_window_armed
            and self._rb_origin is not None
            and event.button() == Qt.LeftButton
        ):
            assert self._rubber_band is not None
            band = QRect(self._rb_origin, event.pos()).normalized()
            self._rubber_band.hide()
            self._rb_origin = None
            self._zoom_window_armed = False
            self.viewport().unsetCursor()
            if band.width() > 4 and band.height() > 4:
                target = self.mapToScene(band).boundingRect()
                if target.width() > 0 and target.height() > 0:
                    vp = self.viewport()
                    view_scale = min(
                        vp.width() / target.width(),
                        vp.height() / target.height(),
                    )
                    self._apply_zoom(view_scale * self._render_dpi / 72.0)
                    self.centerOn(target.center())
            # Restore the tool cursor now that zoom-window is finished.
            self.viewport().setCursor(self._tool_cursor)
            event.accept()
            return

        if self._panning and event.button() in (
            Qt.LeftButton,
            Qt.MiddleButton,
        ):
            self._panning = False
            self._pan_start = None
            if self._space_held:
                self.viewport().setCursor(Qt.OpenHandCursor)
            else:
                self.viewport().setCursor(self._tool_cursor)
            event.accept()
            return

        super().mouseReleaseEvent(event)
        self._measurement_hud.hide()
