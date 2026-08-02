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
    QPen,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
)

from annoter.model.styles import HandleRole, TextAlign, TextBorder
from annoter.model.tolerance import Tolerance, ToleranceMode
from annoter.views.items.base import AnnotationItem
from annoter.views.items.text_objects import (
    insert_tolerance,
    iter_fragments,
    refresh_tolerances,
    tolerance_from_format,
)


_BORDER_PAD = 3.0  # gap between the text frame and its optional outline


def circumscribed_circle_rect(rect: QRectF) -> QRectF:
    """Square rect of the CIRCLE passing through `rect`'s corners.

    Per user feedback the round outline is a true circle (like circled
    revision marks on drawings), not an ellipse; its diameter is the
    rect's diagonal so the text is never clipped. Shared with the line
    end-label outlines.
    """
    d = (rect.width() ** 2 + rect.height() ** 2) ** 0.5
    c = rect.center()
    return QRectF(c.x() - d / 2.0, c.y() - d / 2.0, d, d)


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
    """QObject relay so the plain QGraphicsItem can expose Qt signals."""

    editingFinished = Signal(str)
    # Emitted when the item enters edit mode, so MainWindow can raise
    # the contextual edit toolbar over it.
    editingStarted = Signal(object)  # the TextAnnotationItem


class _InnerTextItem(QGraphicsTextItem):
    def __init__(self, parent: TextAnnotationItem) -> None:
        super().__init__(parent)
        font = QFont(DEFAULT_TEXT_FONT_FAMILY, DEFAULT_TEXT_POINT_SIZE)
        font.setStyleHint(QFont.Helvetica)
        self.setFont(font)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        # A popup -- one of the contextual edit toolbar's menus -- takes
        # keyboard focus off the graphics view, which makes the scene
        # clear its focus item. That is NOT the end of the edit session:
        # ending it here would emit editingFinished and roll back a
        # still-empty new annotation the moment the user opens the
        # Symbol menu. Keep the interaction flags (hence ItemIsFocusable)
        # so Qt hands focus straight back when the popup closes.
        if event.reason() == Qt.PopupFocusReason:
            super().focusOutEvent(event)
            return
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
        self.editingStarted = self._notifier.editingStarted

        self._font_family: str = DEFAULT_TEXT_FONT_FAMILY
        self._font_size: int = DEFAULT_TEXT_POINT_SIZE
        self._bold: bool = False
        self._italic: bool = False
        self._align: TextAlign = TextAlign.LEFT
        # Optional outline around the text (none / box / ellipse) --
        # e.g. circling a revision mark on a drawing.
        self._border: TextBorder = TextBorder.NONE
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
        # Inline runs are rasterized against the font they were made
        # with, so they have to be redrawn or they stop matching the
        # text around them.
        self._refresh_runs()
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
        """Plain-text form; a tolerance run renders as "+0.10/-0.05".

        This is what the annotation list, the empty-rollback check and
        the PDF `/Contents` string all read, so inline runs must degrade
        to something a human (and a text search) can make sense of --
        never the bare object-replacement character Qt stores.
        """
        doc = self._inner.document()
        lines: list[str] = []
        block = doc.begin()
        while block.isValid():
            buf: list[str] = []
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    tol = tolerance_from_format(fragment.charFormat())
                    buf.append(
                        tol.plain() if tol is not None else fragment.text()
                    )
                it += 1
            lines.append("".join(buf))
            block = block.next()
        return "\n".join(lines)

    def set_text(self, text: str) -> None:
        self.prepareGeometryChange()
        self._inner.setPlainText(text)
        self.update()

    # ------------------------------------------------------------------
    # inline runs (symbols and tolerances)
    # ------------------------------------------------------------------
    def _insertion_cursor(self) -> QTextCursor:
        """Caret to insert at: the live one while editing, else the end.

        Outside edit mode the item's cursor sits at position 0 (that is
        where `setPlainText` leaves it), so inserting there would prefix
        the annotation instead of appending to it.
        """
        cursor = self._inner.textCursor()
        if self._inner.textInteractionFlags() == Qt.NoTextInteraction:
            cursor.movePosition(QTextCursor.End)
        return cursor

    def insert_symbol(self, symbol: str) -> None:
        """Insert a plain character at the caret (or at the end)."""
        if not symbol:
            return
        self.prepareGeometryChange()
        cursor = self._insertion_cursor()
        cursor.insertText(symbol)
        self._inner.setTextCursor(cursor)
        self.update()

    def insert_tolerance(self, tol: Tolerance) -> None:
        """Insert a tolerance run at the caret (or at the end)."""
        if tol.is_empty():
            return
        self.prepareGeometryChange()
        cursor = self._insertion_cursor()
        insert_tolerance(cursor, tol, self._inner.font(), self._color)
        self._inner.setTextCursor(cursor)
        self.update()

    def tolerances(self) -> list[Tolerance]:
        return [
            tol
            for _b, fragment in iter_fragments(self._inner.document())
            if (tol := tolerance_from_format(fragment.charFormat())) is not None
        ]

    def has_tolerance_runs(self) -> bool:
        return bool(self.tolerances())

    def has_stacked_runs(self) -> bool:
        """True when a run cannot be written as a plain PDF string.

        A symmetric tolerance is just "±0.05" and survives as native
        FreeText; a stacked bilateral one does not, and is what forces
        the rasterized-appearance path on save.
        """
        return any(
            t.mode is ToleranceMode.BILATERAL for t in self.tolerances()
        )

    def rich_runs(self) -> list[dict]:
        """Serialize the document as ordered runs, for PDF persistence."""
        runs: list[dict] = []
        doc = self._inner.document()
        block = doc.begin()
        first = True
        while block.isValid():
            if not first:
                runs.append({"br": 1})
            first = False
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    tol = tolerance_from_format(fragment.charFormat())
                    if tol is not None:
                        runs.append({"tol": tol.to_dict()})
                    elif fragment.text():
                        runs.append({"t": fragment.text()})
                it += 1
            block = block.next()
        return runs

    def set_rich_runs(self, runs: list[dict]) -> None:
        """Rebuild the document from `rich_runs` output."""
        self.prepareGeometryChange()
        doc = self._inner.document()
        doc.clear()
        cursor = QTextCursor(doc)
        font = self._inner.font()
        for run in runs:
            if "br" in run:
                cursor.insertBlock()
            elif "tol" in run:
                insert_tolerance(
                    cursor, Tolerance.from_dict(run["tol"]), font, self._color
                )
            elif "t" in run:
                cursor.insertText(str(run["t"]))
        self._sync_inner_align()
        self.update()

    def _refresh_runs(self) -> None:
        """Re-render inline runs after a font or colour change."""
        if refresh_tolerances(
            self._inner.document(), self._inner.font(), self._color
        ):
            self.prepareGeometryChange()
            self.update()

    def begin_edit(self) -> None:
        self._inner.setTextInteractionFlags(Qt.TextEditorInteraction)
        self._inner.setFocus()
        cursor = self._inner.textCursor()
        cursor.movePosition(QTextCursor.End)
        self._inner.setTextCursor(cursor)
        self.editingStarted.emit(self)

    # Alias used by the view's typing-to-edit path so shapes and text
    # items share the same method name.
    def begin_text_edit(self) -> None:
        self.begin_edit()

    def is_editing(self) -> bool:
        return self._inner.textInteractionFlags() != Qt.NoTextInteraction

    def refocus_editor(self) -> None:
        """Hand the caret back after a popup stole keyboard focus.

        Unlike `begin_edit` this does not restart the session (no
        `editingStarted`, no cursor jump to the end), so the user resumes
        typing exactly where they were.
        """
        if self.is_editing():
            self._inner.setFocus()

    def scale_geometry(self, s: float) -> None:
        super().scale_geometry(s)
        self.set_font_size(max(4, round(self._font_size * s)))
        if self._text_width > 0:
            self._text_width *= s
            self._inner.setTextWidth(self._text_width)

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
        self._refresh_runs()

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # optional outline (box / ellipse)
    # ------------------------------------------------------------------
    def border(self) -> TextBorder:
        return self._border

    def set_border(self, border: TextBorder) -> None:
        if border is self._border:
            return
        self.prepareGeometryChange()
        self._border = border
        self.update()

    def _border_draw_rect(self) -> QRectF | None:
        """Rect the outline is drawn in (None when no outline)."""
        if self._border is TextBorder.NONE:
            return None
        padded = self.content_rect().adjusted(
            -_BORDER_PAD, -_BORDER_PAD, _BORDER_PAD, _BORDER_PAD
        )
        if self._border is TextBorder.ELLIPSE:
            return circumscribed_circle_rect(padded)
        return padded

    def boundingRect(self) -> QRectF:
        inner = self._inner.boundingRect()
        if inner.isEmpty():
            fm = QFontMetricsF(self._inner.font())
            base = QRectF(
                0, 0, max(fm.averageCharWidth() * 4, 24), fm.height()
            )
        else:
            base = inner
        outline = self._border_draw_rect()
        if outline is not None:
            pad = self._stroke / 2.0 + 1.0
            base = base.united(outline.adjusted(-pad, -pad, pad, pad))
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
        # The inner QGraphicsTextItem paints itself; we add the optional
        # outline and the selection marker.
        outline = self._border_draw_rect()
        if outline is not None:
            pen = QPen(self._color, max(self._stroke, 1.0))
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            if self._border is TextBorder.ELLIPSE:
                painter.drawEllipse(outline)
            else:
                painter.drawRect(outline)
        self._draw_selection_marker(painter, self.boundingRect())

    def mouseDoubleClickEvent(
        self, event: QGraphicsSceneMouseEvent
    ) -> None:
        self.begin_edit()
        event.accept()

    def clone(self) -> "TextAnnotationItem":
        c = TextAnnotationItem(QPointF(self.pos()), "")
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
        c.set_border(self._border)
        # Rebuild from runs, not from plain text: a copied annotation
        # must keep its tolerance runs editable, not flatten them to
        # "+0.10/-0.05" characters.
        c.set_rich_runs(self.rich_runs())
        if self._text_width > 0:
            c._text_width = self._text_width
            c._inner.setTextWidth(self._text_width)
        return c
