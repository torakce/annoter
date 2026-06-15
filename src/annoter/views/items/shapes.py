"""Rectangle and ellipse annotation items.

Shapes can carry an optional text label rendered inside the rect, with
PowerPoint-style centered alignment. Double-click to edit; focus-out
commits. The label is persisted via the props payload (pdf_export) so
round-trips preserve it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen, QTextCursor
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
)

from annoter.model.styles import HandleRole
from annoter.views.items.base import AnnotationItem


_DEFAULT_LABEL_FONT_FAMILY = "Helvetica"
_DEFAULT_LABEL_POINT_SIZE = 11
_LABEL_PADDING = 4.0

# Target scallop radius (page pixels) for revision clouds.
CLOUD_SCALLOP_RADIUS = 8.0


def build_cloud_path(rect: QRectF, radius: float) -> QPainterPath:
    """Revision-cloud outline: outward convex arcs along the rect
    perimeter, traversed clockwise.

    Each scallop arc is sampled into a few short line segments so the
    result is independent of Qt's (y-down) arc-angle conventions and
    fills cleanly. Used both by `CloudItem` and the tool icon.
    """
    path = QPainterPath()
    r = QRectF(rect).normalized()
    if r.width() < 1.0 or r.height() < 1.0:
        path.addRect(r)
        return path

    corners = [
        QPointF(r.left(), r.top()),
        QPointF(r.right(), r.top()),
        QPointF(r.right(), r.bottom()),
        QPointF(r.left(), r.bottom()),
    ]
    target = max(2.0, 2.0 * radius)  # nominal scallop chord length
    samples = 8
    started = False
    for i in range(4):
        a = corners[i]
        b = corners[(i + 1) % 4]
        ex, ey = b.x() - a.x(), b.y() - a.y()
        length = math.hypot(ex, ey)
        if length < 1e-6:
            continue
        ex, ey = ex / length, ey / length
        # Outward normal for clockwise traversal in screen (y-down) coords.
        nx, ny = ey, -ex
        n = max(1, round(length / target))
        step = length / n
        rad = step / 2.0
        for k in range(n):
            base_x = a.x() + ex * step * k
            base_y = a.y() + ey * step * k
            mid_x = base_x + ex * rad
            mid_y = base_y + ey * rad
            a0 = math.atan2(base_y - mid_y, base_x - mid_x)
            # Sweep the half-circle whose midpoint bulges outward.
            cand = a0 + math.pi / 2.0
            sweep = math.pi
            if math.cos(cand) * nx + math.sin(cand) * ny <= 0.0:
                sweep = -math.pi
            if not started:
                path.moveTo(base_x, base_y)
                started = True
            for j in range(1, samples + 1):
                ang = a0 + sweep * j / samples
                path.lineTo(
                    mid_x + rad * math.cos(ang),
                    mid_y + rad * math.sin(ang),
                )
    path.closeSubpath()
    return path


class _ShapeTextItem(QGraphicsTextItem):
    """Inner editable text node centered inside a `_ShapeItem`'s rect."""

    def __init__(self, owner: "_ShapeItem") -> None:
        super().__init__(owner)
        self._owner = owner
        font = QFont(_DEFAULT_LABEL_FONT_FAMILY, _DEFAULT_LABEL_POINT_SIZE)
        font.setStyleHint(QFont.Helvetica)
        self.setFont(font)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setDefaultTextColor(QColor("#212121"))
        opt = self.document().defaultTextOption()
        opt.setAlignment(Qt.AlignCenter)
        self.document().setDefaultTextOption(opt)

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        super().focusOutEvent(event)
        if self._owner is not None:
            self._owner._on_inner_edit_finished(self.toPlainText())


class _ShapeItem(AnnotationItem):
    def __init__(
        self, rect: QRectF, parent: QGraphicsItem | None = None
    ) -> None:
        super().__init__(parent)
        self._rect: QRectF = QRectF(rect).normalized()
        self._fill_enabled: bool = False
        self._fill_color: QColor = QColor("#FFEB3B")
        self._text: str = ""
        self._label_font_size: int = _DEFAULT_LABEL_POINT_SIZE
        self._inner: _ShapeTextItem | None = None

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def set_rect(self, rect: QRectF) -> None:
        new_rect = QRectF(rect).normalized()
        if new_rect == self._rect:
            return
        self.prepareGeometryChange()
        self._rect = new_rect
        self._sync_inner_layout()
        self.update()

    # ------------------------------------------------------------------
    # text label
    # ------------------------------------------------------------------
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        s = str(text)
        if s == self._text and (s == "" or self._inner is not None):
            return
        self._text = s
        if s == "" and self._inner is not None:
            self._inner.setPlainText("")
        elif s:
            self._ensure_inner()
            self._inner.setPlainText(s)  # type: ignore[union-attr]
        self._sync_inner_layout()
        self.update()

    def label_font_size(self) -> int:
        return self._label_font_size

    def set_label_font_size(self, size: int) -> None:
        s = max(4, int(size))
        if s == self._label_font_size:
            return
        self._label_font_size = s
        if self._inner is not None:
            font = self._inner.font()
            font.setPointSize(s)
            self._inner.setFont(font)
            self._sync_inner_layout()
            self.update()

    def _ensure_inner(self) -> None:
        if self._inner is None:
            self._inner = _ShapeTextItem(self)
            font = self._inner.font()
            font.setPointSize(self._label_font_size)
            self._inner.setFont(font)
        # Inner color tracks the shape's stroke color for visibility.
        self._inner.setDefaultTextColor(self._color)
        # CRITICAL: position the inner inside the shape's local _rect.
        # Without this, the inner sits at local (0, 0) -- which is the
        # PAGE origin, since shapes keep pos()=(0,0) and bake their
        # position into _rect. The edit cursor would then appear at the
        # corner of the page instead of inside the shape.
        self._sync_inner_layout()

    def _sync_inner_layout(self) -> None:
        if self._inner is None:
            return
        width = max(0.0, self._rect.width() - 2 * _LABEL_PADDING)
        self._inner.setTextWidth(width)
        block_h = self._inner.boundingRect().height()
        x = self._rect.left() + _LABEL_PADDING
        y = self._rect.center().y() - block_h / 2.0
        self._inner.setPos(x, y)

    def begin_text_edit(self) -> None:
        """Enter inline edit mode on the label (creates inner if needed)."""
        self._ensure_inner()
        assert self._inner is not None
        self._inner.setTextInteractionFlags(Qt.TextEditorInteraction)
        self._inner.setFocus()
        cursor = self._inner.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._inner.setTextCursor(cursor)

    def start_typing(self, text: str) -> None:
        """Enter edit mode and append `text` at the end of the label.

        Bound to the view's keyPressEvent: typing while a shape is
        selected drops the user straight into edit mode with the typed
        character already inserted.
        """
        self.begin_text_edit()
        if not text:
            return
        assert self._inner is not None
        cursor = self._inner.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self._inner.setTextCursor(cursor)

    def _on_inner_edit_finished(self, txt: str) -> None:
        self._text = txt
        self._sync_inner_layout()
        self.update()

    # Keep inner text color in sync with the shape stroke color.
    def set_color(self, color: QColor) -> None:
        super().set_color(color)
        if self._inner is not None:
            self._inner.setDefaultTextColor(self._color)

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        self.begin_text_edit()
        event.accept()

    # ------------------------------------------------------------------
    # fill
    # ------------------------------------------------------------------
    def fill_enabled(self) -> bool:
        return self._fill_enabled

    def set_fill_enabled(self, enabled: bool) -> None:
        if bool(enabled) == self._fill_enabled:
            return
        self._fill_enabled = bool(enabled)
        self.update()

    def fill_color(self) -> QColor:
        return QColor(self._fill_color)

    def set_fill_color(self, color: QColor) -> None:
        c = QColor(color)
        if c == self._fill_color:
            return
        self._fill_color = c
        self.update()

    def boundingRect(self) -> QRectF:
        m = self._stroke / 2.0 + 1.0 + self.handles_extent()
        return self._rect.adjusted(-m, -m, m, m)

    def _pen(self) -> QPen:
        pen = QPen(self._color, self._stroke)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        return self._apply_dash(pen)

    def _brush(self) -> QBrush:
        if self._fill_enabled:
            return QBrush(self._fill_color)
        return QBrush(Qt.NoBrush)

    # ------------------------------------------------------------------
    # resize handles (8 around the bounding rect)
    # ------------------------------------------------------------------
    def handle_positions(self) -> dict[HandleRole, QPointF]:
        r = self._rect
        cx = r.x() + r.width() / 2.0
        cy = r.y() + r.height() / 2.0
        return {
            HandleRole.TOP_LEFT: QPointF(r.left(), r.top()),
            HandleRole.TOP: QPointF(cx, r.top()),
            HandleRole.TOP_RIGHT: QPointF(r.right(), r.top()),
            HandleRole.RIGHT: QPointF(r.right(), cy),
            HandleRole.BOTTOM_RIGHT: QPointF(r.right(), r.bottom()),
            HandleRole.BOTTOM: QPointF(cx, r.bottom()),
            HandleRole.BOTTOM_LEFT: QPointF(r.left(), r.bottom()),
            HandleRole.LEFT: QPointF(r.left(), cy),
        }

    def apply_resize(self, role: HandleRole, local_pos: QPointF) -> None:
        r = QRectF(self._rect)
        x1, y1, x2, y2 = r.left(), r.top(), r.right(), r.bottom()
        px, py = local_pos.x(), local_pos.y()
        if role in (HandleRole.TOP_LEFT, HandleRole.LEFT, HandleRole.BOTTOM_LEFT):
            x1 = px
        if role in (
            HandleRole.TOP_RIGHT,
            HandleRole.RIGHT,
            HandleRole.BOTTOM_RIGHT,
        ):
            x2 = px
        if role in (HandleRole.TOP_LEFT, HandleRole.TOP, HandleRole.TOP_RIGHT):
            y1 = py
        if role in (
            HandleRole.BOTTOM_LEFT,
            HandleRole.BOTTOM,
            HandleRole.BOTTOM_RIGHT,
        ):
            y2 = py
        new_rect = QRectF(x1, y1, x2 - x1, y2 - y1).normalized()
        # Keep a minimum visible footprint so the user can grab handles.
        min_w = 2.0
        min_h = 2.0
        if new_rect.width() < min_w:
            new_rect.setWidth(min_w)
        if new_rect.height() < min_h:
            new_rect.setHeight(min_h)
        self.set_rect(new_rect)

    def geom_snapshot(self) -> object:
        return QRectF(self._rect)

    def apply_geom(self, snapshot: object) -> None:
        if isinstance(snapshot, QRectF):
            self.set_rect(snapshot)


class RectangleItem(_ShapeItem):
    KIND = "rect"

    def __init__(
        self, rect: QRectF, parent: QGraphicsItem | None = None
    ) -> None:
        super().__init__(rect, parent)
        self._corner_radius: float = 0.0

    def corner_radius(self) -> float:
        return self._corner_radius

    def set_corner_radius(self, r: float) -> None:
        v = max(0.0, float(r))
        if v == self._corner_radius:
            return
        self._corner_radius = v
        self.update()

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(self._brush())
        if self._corner_radius > 0.0:
            painter.drawRoundedRect(
                self._rect, self._corner_radius, self._corner_radius
            )
        else:
            painter.drawRect(self._rect)
        self._draw_selection_marker(painter, self.boundingRect())

    def clone(self) -> "RectangleItem":
        c = RectangleItem(QRectF(self._rect))
        self._copy_base_style_into(c)
        c.set_fill_enabled(self._fill_enabled)
        c.set_fill_color(self._fill_color)
        c.set_corner_radius(self._corner_radius)
        c.set_label_font_size(self._label_font_size)
        c.set_text(self._text)
        return c


class EllipseItem(_ShapeItem):
    KIND = "ellipse"

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(self._brush())
        painter.drawEllipse(self._rect)
        self._draw_selection_marker(painter, self.boundingRect())

    def clone(self) -> "EllipseItem":
        c = EllipseItem(QRectF(self._rect))
        self._copy_base_style_into(c)
        c.set_fill_enabled(self._fill_enabled)
        c.set_fill_color(self._fill_color)
        c.set_label_font_size(self._label_font_size)
        c.set_text(self._text)
        return c


class CloudItem(_ShapeItem):
    """Revision cloud: a rectangle drawn with a scalloped (cloudy) border.

    Reuses the shape resize/fill machinery; only the outline differs.
    Persisted as a native PDF Polygon annotation with a cloudy border
    effect so Acrobat/Foxit display the same scallops. Unlike the other
    shapes, clouds carry no inline text label.
    """

    KIND = "cloud"

    def scallop_radius(self) -> float:
        return CLOUD_SCALLOP_RADIUS

    def boundingRect(self) -> QRectF:
        # The scallops bulge outward past `_rect`, so widen the margin.
        m = (
            self._stroke / 2.0
            + self.scallop_radius()
            + 1.0
            + self.handles_extent()
        )
        return self._rect.adjusted(-m, -m, m, m)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(self._pen())
        painter.setBrush(self._brush())
        painter.drawPath(build_cloud_path(self._rect, self.scallop_radius()))
        self._draw_selection_marker(painter, self.boundingRect())

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        # Clouds have no text label; swallow the double-click so the base
        # class does not drop the user into label-edit mode.
        event.accept()

    def clone(self) -> "CloudItem":
        c = CloudItem(QRectF(self._rect))
        self._copy_base_style_into(c)
        c.set_fill_enabled(self._fill_enabled)
        c.set_fill_color(self._fill_color)
        return c
