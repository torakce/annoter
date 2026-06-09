"""QIcon factory: builds toolbar / combo / tool-button icons in code.

Avoids bundling raster assets — every icon is drawn on demand with
QPainter so it scales with the user's DPI and follows the active theme.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

from annoter.controllers.tools import Tool
from annoter.model.styles import DASH_PATTERNS, DashStyle, EndStyle, TextAlign


_DEFAULT_FG = QColor("#212121")


def _pixmap(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    return pm


def _begin(pm: QPixmap) -> QPainter:
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return p


# ----------------------------------------------------------------------
# dash style
# ----------------------------------------------------------------------
def dash_icon(
    style: DashStyle, size: int = 64, color: QColor | None = None
) -> QIcon:
    """Horizontal line preview of a DashStyle."""
    pm = _pixmap(size)
    p = _begin(pm)
    pen = QPen(color if color is not None else _DEFAULT_FG)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.FlatCap)
    pattern = DASH_PATTERNS[style]
    if pattern:
        pen.setDashPattern(pattern)
    p.setPen(pen)
    y = size / 2.0
    margin = size * 0.1
    p.drawLine(QPointF(margin, y), QPointF(size - margin, y))
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------
# end style (arrow heads / line caps)
# ----------------------------------------------------------------------
def end_icon(
    style: EndStyle, size: int = 64, color: QColor | None = None
) -> QIcon:
    """Line preview with the chosen end decoration on the right."""
    pm = _pixmap(size)
    p = _begin(pm)
    c = color if color is not None else _DEFAULT_FG
    pen = QPen(c)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)

    y = size / 2.0
    left = size * 0.15
    right = size * 0.7
    p.drawLine(QPointF(left, y), QPointF(right, y))

    anchor = QPointF(right, y)
    head = size * 0.22
    if style is EndStyle.NONE:
        pass
    elif style in (EndStyle.OPEN_ARROW, EndStyle.CLOSED_ARROW):
        a = math.radians(22.0)
        h1 = QPointF(anchor.x() + head * math.cos(math.pi - a),
                     anchor.y() + head * math.sin(math.pi - a))
        h2 = QPointF(anchor.x() + head * math.cos(math.pi + a),
                     anchor.y() + head * math.sin(math.pi + a))
        poly = QPolygonF([anchor, h1, h2])
        if style is EndStyle.CLOSED_ARROW:
            p.setBrush(c)
        else:
            p.setBrush(Qt.NoBrush)
        p.drawPolygon(poly)
    elif style is EndStyle.BUTT:
        half = head * 0.55
        p.drawLine(
            QPointF(anchor.x(), anchor.y() - half),
            QPointF(anchor.x(), anchor.y() + half),
        )
    elif style is EndStyle.SLASH:
        half = head * 0.55
        ang = math.radians(60.0)
        dx = half * math.cos(ang)
        dy = half * math.sin(ang)
        p.drawLine(
            QPointF(anchor.x() + dx, anchor.y() - dy),
            QPointF(anchor.x() - dx, anchor.y() + dy),
        )
    elif style is EndStyle.DIAMOND:
        half = head * 0.5
        poly = QPolygonF([
            QPointF(anchor.x() + half, anchor.y()),
            QPointF(anchor.x(), anchor.y() - half),
            QPointF(anchor.x() - half, anchor.y()),
            QPointF(anchor.x(), anchor.y() + half),
        ])
        p.setBrush(c)
        p.drawPolygon(poly)
    elif style is EndStyle.CIRCLE:
        r = head * 0.4
        p.setBrush(c)
        p.drawEllipse(anchor, r, r)
    elif style is EndStyle.SQUARE:
        half = head * 0.4
        p.setBrush(c)
        p.drawRect(
            QRectF(
                anchor.x() - half,
                anchor.y() - half,
                2 * half,
                2 * half,
            )
        )
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------
# text alignment
# ----------------------------------------------------------------------
def align_icon(
    align: TextAlign, size: int = 64, color: QColor | None = None
) -> QIcon:
    """Stack of 4 horizontal bars aligned per the TextAlign enum."""
    pm = _pixmap(size)
    p = _begin(pm)
    c = color if color is not None else _DEFAULT_FG
    pen = QPen(c)
    pen.setWidthF(2.0)
    pen.setCapStyle(Qt.FlatCap)
    p.setPen(pen)

    margin = size * 0.15
    available = size - 2 * margin
    bar_count = 4
    spacing = available / (bar_count + 1)
    full = available
    short = available * 0.6
    widths = [full, short, full, short]
    for i, w in enumerate(widths):
        y = margin + spacing * (i + 1)
        if align is TextAlign.LEFT:
            x1 = margin
        elif align is TextAlign.RIGHT:
            x1 = size - margin - w
        else:  # CENTER
            x1 = (size - w) / 2.0
        p.drawLine(QPointF(x1, y), QPointF(x1 + w, y))
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------
# tool palette icons
# ----------------------------------------------------------------------
def tool_icon(tool: Tool, size: int = 64, color: QColor | None = None) -> QIcon:
    pm = _pixmap(size)
    p = _begin(pm)
    c = color if color is not None else _DEFAULT_FG
    pen = QPen(c)
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    margin = size * 0.18
    inner = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)

    if tool is Tool.SELECT:
        # Mouse arrow cursor glyph.
        p.setBrush(c)
        tip = QPointF(size * 0.28, size * 0.22)
        poly = QPolygonF([
            tip,
            QPointF(size * 0.28, size * 0.72),
            QPointF(size * 0.42, size * 0.58),
            QPointF(size * 0.56, size * 0.78),
            QPointF(size * 0.64, size * 0.74),
            QPointF(size * 0.50, size * 0.54),
            QPointF(size * 0.66, size * 0.48),
        ])
        p.drawPolygon(poly)
    elif tool is Tool.RECTANGLE:
        p.drawRect(inner)
    elif tool is Tool.ELLIPSE:
        p.drawEllipse(inner)
    elif tool is Tool.LINE:
        p.drawLine(
            QPointF(margin, size - margin),
            QPointF(size - margin, margin),
        )
    elif tool is Tool.ARROW:
        a = QPointF(margin, size - margin)
        b = QPointF(size - margin, margin)
        p.drawLine(a, b)
        # arrow head at b
        ang = math.atan2(b.y() - a.y(), b.x() - a.x())
        head = size * 0.22
        h_ang = math.radians(25.0)
        h1 = QPointF(
            b.x() - head * math.cos(ang - h_ang),
            b.y() - head * math.sin(ang - h_ang),
        )
        h2 = QPointF(
            b.x() - head * math.cos(ang + h_ang),
            b.y() - head * math.sin(ang + h_ang),
        )
        p.setBrush(c)
        p.drawPolygon(QPolygonF([b, h1, h2]))
    elif tool is Tool.TEXT:
        from PySide6.QtGui import QFont

        font = QFont()
        font.setPixelSize(int(size * 0.7))
        font.setBold(True)
        p.setFont(font)
        p.drawText(
            QRectF(0, 0, size, size), Qt.AlignCenter, "T"
        )
    elif tool is Tool.FREEHAND:
        # Two cubic arcs forming a squiggle.
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.moveTo(margin, size * 0.6)
        path.cubicTo(
            QPointF(size * 0.3, margin),
            QPointF(size * 0.45, size - margin),
            QPointF(size * 0.6, size * 0.5),
        )
        path.cubicTo(
            QPointF(size * 0.7, size * 0.25),
            QPointF(size * 0.85, size * 0.65),
            QPointF(size - margin, size * 0.45),
        )
        p.drawPath(path)
    p.end()
    return QIcon(pm)
