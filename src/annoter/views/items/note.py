"""StickyNoteItem: a small comment-bubble marker carrying a text note.

Mirrors Acrobat's sticky note: a fixed-size icon placed on the page,
whose text is shown on hover and edited in a floating popup (see
`views/note_editor.py`). Persisted as a native PDF `Text` annotation so
it opens as a real sticky note in Acrobat/Foxit.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsSceneMouseEvent

from annoter.views.items.base import AnnotationItem


# Icon footprint in page pixels (scales with zoom like every annotation).
_ICON_W = 26.0
_ICON_H = 22.0
_TAIL_H = 6.0


class StickyNoteItem(AnnotationItem):
    KIND = "note"

    def __init__(
        self,
        pos: QPointF,
        text: str = "",
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._text: str = ""
        self.setPos(pos)
        self.set_text(text)
        self._edit_callback = None  # type: ignore[var-annotated]

    # ------------------------------------------------------------------
    # text
    # ------------------------------------------------------------------
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = str(text)
        # Hover tooltip surfaces the note without opening the editor.
        self.setToolTip(self._text)
        self.update()

    def label(self) -> str:
        snippet = self._text.strip().splitlines()[0] if self._text.strip() else ""
        if snippet:
            return f"Note: {snippet[:24]}"
        return "Note"

    # ------------------------------------------------------------------
    # editing hook (double-click)
    # ------------------------------------------------------------------
    def set_edit_callback(self, callback) -> None:  # noqa: ANN001
        self._edit_callback = callback

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        if self._edit_callback is not None:
            self._edit_callback(self)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # geometry / painting
    # ------------------------------------------------------------------
    def content_rect(self) -> QRectF:
        """Bubble body + tail, ignoring handle/selection padding."""
        return QRectF(0.0, 0.0, _ICON_W, _ICON_H + _TAIL_H)

    def boundingRect(self) -> QRectF:
        m = 1.5
        return self.content_rect().adjusted(-m, -m, m, m)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        body = QRectF(0.0, 0.0, _ICON_W, _ICON_H)
        path = QPainterPath()
        radius = 4.0
        path.addRoundedRect(body, radius, radius)
        # Small speech-bubble tail at the lower-left.
        tail = QPolygonF([
            QPointF(_ICON_W * 0.22, _ICON_H - 0.5),
            QPointF(_ICON_W * 0.22, _ICON_H + _TAIL_H),
            QPointF(_ICON_W * 0.46, _ICON_H - 0.5),
        ])
        path.addPolygon(tail)
        path.setFillRule(Qt.WindingFill)

        pen = QPen(self._color.darker(140), 1.2)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QBrush(self._color))
        painter.drawPath(path.simplified())

        # Three "text lines" inside the bubble for legibility.
        line_pen = QPen(self._readable_ink(), 1.0)
        painter.setPen(line_pen)
        for i in range(3):
            y = body.top() + 6.0 + i * 4.5
            painter.drawLine(
                QPointF(body.left() + 5.0, y),
                QPointF(body.right() - 5.0, y),
            )
        self._draw_selection_marker(painter, self.boundingRect())

    def _readable_ink(self) -> QColor:
        """Dark or light line color, whichever contrasts with the fill."""
        c = self._color
        luminance = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return QColor(30, 30, 30) if luminance > 140 else QColor(245, 245, 245)

    def clone(self) -> "StickyNoteItem":
        c = StickyNoteItem(QPointF(self.pos()), self._text)
        c.set_color(self.color())
        c.set_stroke(self.stroke())
        c.set_dash_style(self.dash_style())
        c.set_edit_callback(self._edit_callback)
        return c
