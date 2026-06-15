"""QIcon factory: builds toolbar / combo / tool-button icons in code.

Avoids bundling raster assets — every icon is drawn on demand with
QPainter so it scales with the user's DPI and follows the active theme.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
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
# toolbar action icons
# ----------------------------------------------------------------------
def _arrow_head(
    p: QPainter,
    tip: QPointF,
    angle: float,
    head: float,
    color: QColor,
) -> None:
    a = math.radians(28.0)
    h1 = QPointF(
        tip.x() - head * math.cos(angle - a),
        tip.y() - head * math.sin(angle - a),
    )
    h2 = QPointF(
        tip.x() - head * math.cos(angle + a),
        tip.y() - head * math.sin(angle + a),
    )
    p.setBrush(color)
    p.drawPolygon(QPolygonF([tip, h1, h2]))


def _rotation_arrow(
    p: QPainter, rect: QRectF, color: QColor, clockwise: bool
) -> None:
    """Open arc with an arrowhead at the moving end (undo / redo)."""
    start = 150.0 if clockwise else 30.0
    span = -250.0 if clockwise else 250.0
    p.setBrush(Qt.NoBrush)
    p.drawArc(rect, int(start * 16), int(span * 16))
    end = math.radians((start + span) % 360.0)
    cx, cy = rect.center().x(), rect.center().y()
    rx, ry = rect.width() / 2.0, rect.height() / 2.0
    tip = QPointF(cx + rx * math.cos(end), cy - ry * math.sin(end))
    sgn = -1.0 if clockwise else 1.0
    d = QPointF(-rx * math.sin(end) * sgn, -ry * math.cos(end) * sgn)
    angle = math.atan2(d.y(), d.x())
    _arrow_head(p, tip, angle, rect.width() * 0.36, color)


def action_icon(
    name: str, size: int = 64, color: QColor | None = None
) -> QIcon:
    """Toolbar glyphs drawn in code: open, save, undo, redo, zoom-*."""
    pm = _pixmap(size)
    p = _begin(pm)
    c = color if color is not None else _DEFAULT_FG
    pen = QPen(c)
    pen.setWidthF(size * 0.06)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = float(size)
    m = s * 0.15

    if name == "open":
        path = QPainterPath()
        path.moveTo(m, s * 0.78)
        path.lineTo(m, s * 0.26)
        path.lineTo(s * 0.40, s * 0.26)
        path.lineTo(s * 0.48, s * 0.36)
        path.lineTo(s - m, s * 0.36)
        path.lineTo(s - m, s * 0.78)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "save":
        # Floppy outline with a clipped top-right corner.
        path = QPainterPath()
        path.moveTo(m, m)
        path.lineTo(s * 0.70, m)
        path.lineTo(s - m, s * 0.30)
        path.lineTo(s - m, s - m)
        path.lineTo(m, s - m)
        path.closeSubpath()
        p.drawPath(path)
        p.drawRect(QRectF(s * 0.32, m, s * 0.30, s * 0.20))
        p.drawRect(QRectF(s * 0.28, s * 0.52, s * 0.44, s * 0.33))
    elif name == "undo":
        _rotation_arrow(
            p, QRectF(s * 0.20, s * 0.22, s * 0.58, s * 0.58), c, False
        )
    elif name == "redo":
        _rotation_arrow(
            p, QRectF(s * 0.22, s * 0.22, s * 0.58, s * 0.58), c, True
        )
    elif name in ("zoom-in", "zoom-out"):
        lens_c = QPointF(s * 0.44, s * 0.44)
        lens_r = s * 0.27
        p.drawEllipse(lens_c, lens_r, lens_r)
        handle_start = QPointF(
            lens_c.x() + lens_r * 0.72, lens_c.y() + lens_r * 0.72
        )
        p.drawLine(handle_start, QPointF(s * 0.85, s * 0.85))
        half = lens_r * 0.48
        p.drawLine(
            QPointF(lens_c.x() - half, lens_c.y()),
            QPointF(lens_c.x() + half, lens_c.y()),
        )
        if name == "zoom-in":
            p.drawLine(
                QPointF(lens_c.x(), lens_c.y() - half),
                QPointF(lens_c.x(), lens_c.y() + half),
            )
    elif name == "zoom-fit":
        # Four corner brackets, fullscreen-style.
        k = s * 0.22
        for cx, cy, dx, dy in (
            (m, m, 1, 1),
            (s - m, m, -1, 1),
            (m, s - m, 1, -1),
            (s - m, s - m, -1, -1),
        ):
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx * k, cy))
            p.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * k))
    elif name == "zoom-actual":
        font = QFont()
        font.setPixelSize(int(s * 0.46))
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "1:1")
    elif name == "confirm":
        pen.setWidthF(size * 0.09)
        p.setPen(pen)
        path = QPainterPath()
        path.moveTo(s * 0.22, s * 0.54)
        path.lineTo(s * 0.42, s * 0.74)
        path.lineTo(s * 0.78, s * 0.28)
        p.drawPath(path)
    elif name == "cancel":
        pen.setWidthF(size * 0.09)
        p.setPen(pen)
        p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
        p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))
    p.end()
    return QIcon(pm)


# ----------------------------------------------------------------------
# GD&T characteristic symbols
# ----------------------------------------------------------------------
def gdt_symbol_icon(
    characteristic, size: int = 28, color: QColor | None = None
) -> QIcon:
    """ISO 1101 characteristic symbol, scaled from its unit-box path."""
    from annoter.views.items.gdt_symbols import symbol_path

    pm = _pixmap(size)
    p = _begin(pm)
    pen = QPen(color if color is not None else _DEFAULT_FG)
    pen.setCosmetic(True)
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    inset = size * 0.12
    p.translate(inset, inset)
    p.scale(size - 2 * inset, size - 2 * inset)
    p.drawPath(symbol_path(characteristic))
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
    elif tool is Tool.CLOUD:
        from annoter.views.items.shapes import build_cloud_path

        p.drawPath(build_cloud_path(inner, radius=size * 0.12))
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
    elif tool is Tool.POLYLINE:
        # Open zig-zag with vertex dots.
        pts = [
            QPointF(margin, size - margin),
            QPointF(size * 0.38, margin),
            QPointF(size * 0.62, size * 0.62),
            QPointF(size - margin, margin),
        ]
        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        p.drawPath(path)
        p.setBrush(c)
        for pt in pts:
            p.drawEllipse(pt, size * 0.05, size * 0.05)
    elif tool is Tool.POLYGON:
        # Closed pentagon.
        cx, cy = size / 2.0, size * 0.52
        r = size * 0.34
        poly = QPolygonF([
            QPointF(
                cx + r * math.cos(math.radians(-90 + 72 * k)),
                cy + r * math.sin(math.radians(-90 + 72 * k)),
            )
            for k in range(5)
        ])
        p.drawPolygon(poly)
    elif tool is Tool.TEXT:
        from PySide6.QtGui import QFont

        font = QFont()
        font.setPixelSize(int(size * 0.7))
        font.setBold(True)
        p.setFont(font)
        p.drawText(
            QRectF(0, 0, size, size), Qt.AlignCenter, "T"
        )
    elif tool is Tool.CALLOUT:
        # Text box in the upper area with a leader line + arrow to the
        # lower-left feature point.
        box = QRectF(size * 0.34, margin, size * 0.50, size * 0.34)
        p.drawRoundedRect(box, size * 0.05, size * 0.05)
        conn = QPointF(box.left(), box.bottom())
        tip = QPointF(margin, size - margin)
        p.drawLine(conn, tip)
        ang = math.atan2(tip.y() - conn.y(), tip.x() - conn.x())
        head = size * 0.2
        h_ang = math.radians(24.0)
        h1 = QPointF(
            tip.x() - head * math.cos(ang - h_ang),
            tip.y() - head * math.sin(ang - h_ang),
        )
        h2 = QPointF(
            tip.x() - head * math.cos(ang + h_ang),
            tip.y() - head * math.sin(ang + h_ang),
        )
        p.setBrush(c)
        p.drawPolygon(QPolygonF([tip, h1, h2]))
    elif tool is Tool.GDT:
        # Miniature feature control frame: symbol cell + value cell.
        from annoter.model.gdt import Characteristic
        from annoter.views.items.gdt_symbols import symbol_path

        frame = QRectF(size * 0.08, size * 0.30, size * 0.84, size * 0.40)
        divider_x = frame.left() + frame.height()
        p.drawRect(frame)
        p.drawLine(
            QPointF(divider_x, frame.top()),
            QPointF(divider_x, frame.bottom()),
        )
        cell = QRectF(
            frame.left(), frame.top(), frame.height(), frame.height()
        )
        inset = cell.height() * 0.2
        target = cell.adjusted(inset, inset, -inset, -inset)
        p.save()
        p.translate(target.left(), target.top())
        p.scale(target.width(), target.height())
        sym_pen = QPen(c)
        sym_pen.setCosmetic(True)
        sym_pen.setWidthF(1.4)
        p.setPen(sym_pen)
        p.drawPath(symbol_path(Characteristic.POSITION))
        p.restore()
        mid_y = frame.center().y()
        p.drawLine(
            QPointF(divider_x + size * 0.08, mid_y),
            QPointF(frame.right() - size * 0.08, mid_y),
        )
    elif tool is Tool.STICKY_NOTE:
        # Speech bubble with a tail and a couple of text lines.
        body = QRectF(margin, margin, size - 2 * margin, size * 0.5)
        p.drawRoundedRect(body, size * 0.08, size * 0.08)
        tail = QPolygonF([
            QPointF(body.left() + body.width() * 0.2, body.bottom()),
            QPointF(
                body.left() + body.width() * 0.2,
                body.bottom() + size * 0.18,
            ),
            QPointF(body.left() + body.width() * 0.46, body.bottom()),
        ])
        p.setBrush(c)
        p.drawPolygon(tail)
        p.setBrush(Qt.NoBrush)
        line_pen = QPen(c)
        line_pen.setWidthF(1.6)
        p.setPen(line_pen)
        for i in range(2):
            y = body.top() + body.height() * (0.38 + i * 0.3)
            p.drawLine(
                QPointF(body.left() + size * 0.12, y),
                QPointF(body.right() - size * 0.12, y),
            )
    elif tool is Tool.FREEHAND:
        # Two cubic arcs forming a squiggle.
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
