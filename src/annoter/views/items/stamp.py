"""StampItem: a rubber-stamp marker (APPROVED / REJECTED / ...).

A bold uppercase label inside a double rounded border, tinted with the
stamp color. The box auto-sizes to the text; font size is an editable
property (no resize handles, movable only), mirroring the GD&T frame.
Persisted as a native PDF `Stamp` annotation with a rasterized
appearance stream so Acrobat/Foxit show the actual stamp, plus the text
and size in the `/Subject` JSON for editable reconstruction.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen
from PySide6.QtWidgets import QGraphicsItem

from annoter.views.items.base import AnnotationItem


# Preset stamps mandated by the brief: label -> stamp color. "Custom"
# (no entry here) lets the user type free text and recolor.
STAMP_PRESETS: list[tuple[str, str]] = [
    ("APPROVED", "#2E7D32"),
    ("REJECTED", "#C62828"),
    ("BON POUR EXÉCUTION", "#1565C0"),
]

_DEFAULT_TEXT = "APPROVED"
_DEFAULT_COLOR = "#2E7D32"
_DEFAULT_FONT_SIZE = 16
_PAD_X = 12.0
_PAD_Y = 6.0
_MIN_W = 48.0
_MIN_H = 22.0


class StampItem(AnnotationItem):
    KIND = "stamp"

    def __init__(
        self,
        pos: QPointF,
        text: str = _DEFAULT_TEXT,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._text: str = str(text)
        self._font_size: int = _DEFAULT_FONT_SIZE
        self._color = QColor(_DEFAULT_COLOR)
        self.setPos(pos)

    # ------------------------------------------------------------------
    # text / size
    # ------------------------------------------------------------------
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        s = str(text)
        if s == self._text:
            return
        self.prepareGeometryChange()
        self._text = s
        self.update()

    def font_size(self) -> int:
        return self._font_size

    def set_font_size(self, size: int) -> None:
        s = max(6, int(size))
        if s == self._font_size:
            return
        self.prepareGeometryChange()
        self._font_size = s
        self.update()

    def label(self) -> str:
        return f"Stamp: {self._text}" if self._text else "Stamp"

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------
    def _font(self) -> QFont:
        font = QFont("Helvetica", self._font_size)
        font.setStyleHint(QFont.Helvetica)
        font.setBold(True)
        return font

    def _display_text(self) -> str:
        return self._text.upper()

    def content_rect(self) -> QRectF:
        fm = QFontMetricsF(self._font())
        tw = fm.horizontalAdvance(self._display_text())
        th = fm.height()
        w = max(tw + 2 * _PAD_X, _MIN_W)
        h = max(th + 2 * _PAD_Y, _MIN_H)
        return QRectF(0.0, 0.0, w, h)

    def boundingRect(self) -> QRectF:
        m = self._stroke / 2.0 + 1.0 + self.handles_extent()
        return self.content_rect().adjusted(-m, -m, m, m)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001
        r = self.content_rect()
        radius = 4.0
        # Faint tinted fill.
        tint = QColor(self._color)
        tint.setAlpha(26)
        painter.setBrush(tint)
        outer = QPen(self._color, max(1.6, self._stroke))
        outer.setJoinStyle(Qt.RoundJoin)
        painter.setPen(outer)
        painter.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)
        # Thin inner border for the rubber-stamp look.
        inner = QPen(self._color, 1.0)
        painter.setPen(inner)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(
            r.adjusted(4, 4, -4, -4), radius * 0.6, radius * 0.6
        )
        # Label.
        painter.setPen(QPen(self._color))
        painter.setFont(self._font())
        painter.drawText(r, Qt.AlignCenter, self._display_text())
        self._draw_selection_marker(painter, self.boundingRect())

    def clone(self) -> "StampItem":
        c = StampItem(QPointF(self.pos()), self._text)
        c.set_color(self.color())
        c.set_stroke(self.stroke())
        c.set_dash_style(self.dash_style())
        c.set_font_size(self._font_size)
        return c
