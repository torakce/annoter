"""TextAnnotationItem: free-text bubble, edited inline.

Renders with Helvetica/Arial only so Acrobat round-trip works without
font embedding. Empty text after edit triggers undo rollback (handled
by the dispatch layer that issued the AddAnnotation command).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
)

from annoter.model.styles import HandleRole, TextAlign
from annoter.views.items.base import AnnotationItem


_MIN_TEXT_WIDTH = 24.0
_MIN_TEXT_HEIGHT = 12.0


DEFAULT_TEXT_FONT_FAMILY = "Helvetica"
DEFAULT_TEXT_POINT_SIZE = 12

# Acrobat-friendly font choices that round-trip through FreeText annots
# without embedding (PDF base-14 sans / serif / mono).
TEXT_FONT_FAMILIES = ("Helvetica", "Times New Roman", "Courier New")

_QT_ALIGN: dict[TextAlign, Qt.AlignmentFlag] = {
    TextAlign.LEFT: Qt.AlignLeft,
    TextAlign.CENTER: Qt.AlignHCenter,
    TextAlign.RIGHT: Qt.AlignRight,
}


class _TextNotifier(QObject):
    """QObject relay so the plain QGraphicsItem can expose a Qt signal."""

    editingFinished = Signal(str)


class _InnerTextItem(QGraphicsTextItem):
    def __init__(self, parent: TextAnnotationItem) -> None:
        super().__init__(parent)
        font = QFont(DEFAULT_TEXT_FONT_FAMILY, DEFAULT_TEXT_POINT_SIZE)
        font.setStyleHint(QFont.Helvetica)
        self.setFont(font)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        super().focusOutEvent(event)
        parent = self.parentItem()
        if isinstance(parent, TextAnnotationItem):
            parent.editingFinished.emit(self.toPlainText())


class TextAnnotationItem(AnnotationItem):
    """Free-text annotation. Stores a position and a string."""

    KIND = "text"

    def __init__(
        self,
        pos: QPointF,
        text: str = "",
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._notifier = _TextNotifier()
        self.editingFinished = self._notifier.editingFinished

        self._font_family: str = DEFAULT_TEXT_FONT_FAMILY
        self._font_size: int = DEFAULT_TEXT_POINT_SIZE
        self._bold: bool = False
        self._italic: bool = False
        self._align: TextAlign = TextAlign.LEFT
        # 0 means "auto" -- inner uses its natural width. Set by manual
        # resize to force word-wrap.
        self._text_width: float = 0.0

        self._inner = _InnerTextItem(self)
        self._inner.setPos(0, 0)
        self.setPos(pos)
        self.set_text(text)
        self._sync_inner_font()
        self._sync_inner_align()
        self._sync_inner_color()

    # ------------------------------------------------------------------
    # font / formatting
    # ------------------------------------------------------------------
    def font_family(self) -> str:
        return self._font_family

    def set_font_family(self, family: str) -> None:
        if family == self._font_family:
            return
        self._font_family = str(family)
        self._sync_inner_font()

    def font_size(self) -> int:
        return self._font_size

    def set_font_size(self, size: int) -> None:
        s = max(4, int(size))
        if s == self._font_size:
            return
        self._font_size = s
        self._sync_inner_font()

    def bold(self) -> bool:
        return self._bold

    def set_bold(self, bold: bool) -> None:
        if bool(bold) == self._bold:
            return
        self._bold = bool(bold)
        self._sync_inner_font()

    def italic(self) -> bool:
        return self._italic

    def set_italic(self, italic: bool) -> None:
        if bool(italic) == self._italic:
            return
        self._italic = bool(italic)
        self._sync_inner_font()

    def align(self) -> TextAlign:
        return self._align

    def set_align(self, align: TextAlign) -> None:
        if align is self._align:
            return
        self._align = align
        self._sync_inner_align()

    def _sync_inner_font(self) -> None:
        self.prepareGeometryChange()
        font = QFont(self._font_family, self._font_size)
        font.setStyleHint(QFont.Helvetica)
        font.setBold(self._bold)
        font.setItalic(self._italic)
        self._inner.setFont(font)
        self.update()

    def _sync_inner_align(self) -> None:
        opt = self._inner.document().defaultTextOption()
        opt.setAlignment(_QT_ALIGN[self._align])
        self._inner.document().setDefaultTextOption(opt)
        self.update()

    # ------------------------------------------------------------------
    # text
    # ------------------------------------------------------------------
    def text(self) -> str:
        return self._inner.toPlainText()

    def set_text(self, text: str) -> None:
        self.prepareGeometryChange()
        self._inner.setPlainText(text)
        self.update()

    def begin_edit(self) -> None:
        self._inner.setTextInteractionFlags(Qt.TextEditorInteraction)
        self._inner.setFocus()
        cursor = self._inner.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._inner.setTextCursor(cursor)

    # Alias used by the view's typing-to-edit path so shapes and text
    # items share the same method name.
    def begin_text_edit(self) -> None:
        self.begin_edit()

    def start_typing(self, text: str) -> None:
        """Enter edit mode and append `text` at the end."""
        self.begin_edit()
        if not text:
            return
        cursor = self._inner.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self._inner.setTextCursor(cursor)

    # ------------------------------------------------------------------
    # color override (text uses the inner doc color)
    # ------------------------------------------------------------------
    def set_color(self, color: QColor) -> None:
        super().set_color(color)
        self._sync_inner_color()

    def _sync_inner_color(self) -> None:
        self._inner.setDefaultTextColor(self._color)

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def boundingRect(self) -> QRectF:
        inner = self._inner.boundingRect()
        if inner.isEmpty():
            fm = QFontMetricsF(self._inner.font())
            base = QRectF(
                0, 0, max(fm.averageCharWidth() * 4, 24), fm.height()
            )
        else:
            base = inner
        m = self.handles_extent()
        if m > 0:
            return base.adjusted(-m, -m, m, m)
        return base

    def content_rect(self) -> QRectF:
        """Bounding rect of the text frame, ignoring handle padding."""
        inner = self._inner.boundingRect()
        if inner.isEmpty():
            fm = QFontMetricsF(self._inner.font())
            return QRectF(
                0, 0, max(fm.averageCharWidth() * 4, 24), fm.height()
            )
        return inner

    # ------------------------------------------------------------------
    # resize handles
    # ------------------------------------------------------------------
    # Convention:
    #   - L / R   -> wrap-width only (font preserved, text just rewraps)
    #   - corners -> scale font proportionally to the box growth, and
    #                also update wrap width so the box matches the drag.
    # T/B handles are intentionally omitted: vertical-only resize has no
    # clean meaning for a wrap-driven text frame.
    def handle_positions(self) -> dict[HandleRole, QPointF]:
        r = self.content_rect()
        cy = r.top() + r.height() / 2.0
        return {
            HandleRole.TOP_LEFT: QPointF(r.left(), r.top()),
            HandleRole.TOP_RIGHT: QPointF(r.right(), r.top()),
            HandleRole.RIGHT: QPointF(r.right(), cy),
            HandleRole.BOTTOM_RIGHT: QPointF(r.right(), r.bottom()),
            HandleRole.BOTTOM_LEFT: QPointF(r.left(), r.bottom()),
            HandleRole.LEFT: QPointF(r.left(), cy),
        }

    def apply_resize(
        self, role: HandleRole, local_pos: QPointF
    ) -> None:
        r = self.content_rect()
        x1, y1, x2, y2 = r.left(), r.top(), r.right(), r.bottom()
        px, py = local_pos.x(), local_pos.y()
        if role in (
            HandleRole.TOP_LEFT,
            HandleRole.LEFT,
            HandleRole.BOTTOM_LEFT,
        ):
            x1 = px
        if role in (
            HandleRole.TOP_RIGHT,
            HandleRole.RIGHT,
            HandleRole.BOTTOM_RIGHT,
        ):
            x2 = px
        if role in (HandleRole.TOP_LEFT, HandleRole.TOP_RIGHT):
            y1 = py
        if role in (HandleRole.BOTTOM_LEFT, HandleRole.BOTTOM_RIGHT):
            y2 = py

        cur_w = max(r.width(), 1.0)
        cur_h = max(r.height(), 1.0)
        # Raw (unclamped) deltas drive the scale ratio: clamping new_w
        # at _MIN_TEXT_WIDTH for the wrap setter would also cap the
        # font scale and prevent further shrinking.
        raw_w = max(x2 - x1, 1.0)
        raw_h = max(y2 - y1, 1.0)
        wrap_w = max(raw_w, _MIN_TEXT_WIDTH)

        if role in (HandleRole.LEFT, HandleRole.RIGHT):
            # Wrap-only: stretch / squeeze the box, the text rewraps.
            self._text_width = wrap_w
            self._inner.setTextWidth(wrap_w)
        else:
            # Corner: font follows the box. Pick the ratio of the axis
            # the user actually moved (largest deviation from 1.0).
            # Using max() here would silently ignore vertical-only drags
            # because the horizontal ratio would stay at ~1.
            ratio_w = raw_w / cur_w
            ratio_h = raw_h / cur_h
            scale = (
                ratio_h
                if abs(ratio_h - 1.0) > abs(ratio_w - 1.0)
                else ratio_w
            )
            scale = max(scale, 0.05)
            new_font = max(4, min(200, int(round(self._font_size * scale))))
            if new_font != self._font_size:
                self._font_size = new_font
                self._sync_inner_font()
            self._text_width = wrap_w
            self._inner.setTextWidth(wrap_w)

        # Move pos so the unmoved edge stays anchored in scene space.
        pos = self.pos()
        dx = x1
        dy = y1
        if role in (
            HandleRole.TOP_LEFT,
            HandleRole.LEFT,
            HandleRole.BOTTOM_LEFT,
        ):
            self.setPos(pos.x() + dx, pos.y())
            pos = self.pos()
        if role in (HandleRole.TOP_LEFT, HandleRole.TOP_RIGHT):
            self.setPos(pos.x(), pos.y() + dy)
        self.prepareGeometryChange()
        self.update()

    def geom_snapshot(self) -> object:
        return (
            QPointF(self.pos()),
            int(self._font_size),
            float(self._text_width),
        )

    def apply_geom(self, snapshot: object) -> None:
        if (
            not isinstance(snapshot, tuple)
            or len(snapshot) != 3
            or not isinstance(snapshot[0], QPointF)
        ):
            return
        pos, font_size, text_width = snapshot
        self.setPos(pos)
        if int(font_size) != self._font_size:
            self._font_size = int(font_size)
            self._sync_inner_font()
        self._text_width = float(text_width)
        if self._text_width > 0:
            self._inner.setTextWidth(self._text_width)
        else:
            self._inner.setTextWidth(-1)
        self.prepareGeometryChange()
        self.update()

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        # The inner QGraphicsTextItem paints itself. We only draw the
        # selection marker on top.
        self._draw_selection_marker(painter, self.boundingRect())

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        self.begin_edit()
        event.accept()

    def clone(self) -> "TextAnnotationItem":
        c = TextAnnotationItem(QPointF(self.pos()), self.text())
        # Override _copy_base_style_into's setPos because the ctor already
        # took the position; keep style copies in sync.
        c.set_color(self.color())
        c.set_stroke(self.stroke())
        c.set_dash_style(self.dash_style())
        c.set_font_family(self._font_family)
        c.set_font_size(self._font_size)
        c.set_bold(self._bold)
        c.set_italic(self._italic)
        c.set_align(self._align)
        if self._text_width > 0:
            c._text_width = self._text_width
            c._inner.setTextWidth(self._text_width)
        return c
