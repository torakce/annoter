"""The 14 ISO 1101 characteristic symbols as QPainterPath instances.

Each path is normalized inside the unit box [0, 1] x [0, 1]; the
renderer scales it to the target cell size. Modifiers M / L / P / E are
drawn separately as Unicode enclosed characters in the tolerance /
datum cells.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath

from annoter.model.gdt import Characteristic


def _straightness() -> QPainterPath:
    p = QPainterPath()
    p.moveTo(0.05, 0.5)
    p.lineTo(0.95, 0.5)
    return p


def _flatness() -> QPainterPath:
    p = QPainterPath()
    # Parallelogram (rhomboid).
    p.moveTo(0.20, 0.70)
    p.lineTo(0.95, 0.70)
    p.lineTo(0.80, 0.30)
    p.lineTo(0.05, 0.30)
    p.closeSubpath()
    return p


def _circularity() -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QRectF(0.15, 0.15, 0.70, 0.70))
    return p


def _cylindricity() -> QPainterPath:
    p = QPainterPath()
    # Two oblique slashes flanking a circle: /○/
    p.moveTo(0.05, 0.95)
    p.lineTo(0.30, 0.05)
    p.moveTo(0.70, 0.95)
    p.lineTo(0.95, 0.05)
    p.addEllipse(QRectF(0.32, 0.25, 0.36, 0.50))
    return p


def _profile_line() -> QPainterPath:
    p = QPainterPath()
    # Open upward arc.
    rect = QRectF(0.10, 0.20, 0.80, 0.80)
    p.arcMoveTo(rect, 0.0)
    p.arcTo(rect, 0.0, 180.0)
    return p


def _profile_surface() -> QPainterPath:
    p = QPainterPath()
    # Closed half-disc: arc + chord.
    rect = QRectF(0.10, 0.20, 0.80, 0.80)
    p.arcMoveTo(rect, 0.0)
    p.arcTo(rect, 0.0, 180.0)
    p.closeSubpath()
    return p


def _parallelism() -> QPainterPath:
    p = QPainterPath()
    # Two parallel oblique slashes.
    p.moveTo(0.20, 0.85)
    p.lineTo(0.55, 0.15)
    p.moveTo(0.50, 0.85)
    p.lineTo(0.85, 0.15)
    return p


def _perpendicularity() -> QPainterPath:
    p = QPainterPath()
    # Vertical bar resting on a horizontal one: T flipped (⊥).
    p.moveTo(0.50, 0.10)
    p.lineTo(0.50, 0.85)
    p.moveTo(0.10, 0.85)
    p.lineTo(0.90, 0.85)
    return p


def _angularity() -> QPainterPath:
    p = QPainterPath()
    # 45-degree wedge (∠).
    p.moveTo(0.10, 0.85)
    p.lineTo(0.90, 0.85)
    p.moveTo(0.10, 0.85)
    p.lineTo(0.90, 0.15)
    return p


def _position() -> QPainterPath:
    p = QPainterPath()
    # Circle with a cross extending past it (⌖).
    p.addEllipse(QRectF(0.25, 0.25, 0.50, 0.50))
    p.moveTo(0.50, 0.05)
    p.lineTo(0.50, 0.95)
    p.moveTo(0.05, 0.50)
    p.lineTo(0.95, 0.50)
    return p


def _concentricity() -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QRectF(0.15, 0.15, 0.70, 0.70))
    p.addEllipse(QRectF(0.32, 0.32, 0.36, 0.36))
    return p


def _symmetry() -> QPainterPath:
    p = QPainterPath()
    # Three horizontal stacked bars (≡-like).
    for y in (0.25, 0.50, 0.75):
        p.moveTo(0.10, y)
        p.lineTo(0.90, y)
    return p


def _arrow_tilted(p: QPainterPath, x: float, y_tail: float, length: float) -> None:
    """Draw a 45-degree arrow whose tip is at (x + length, y_tail - length)."""
    tip_x = x + length
    tip_y = y_tail - length
    p.moveTo(x, y_tail)
    p.lineTo(tip_x, tip_y)
    # Arrowhead: two short barbs.
    barb = length * 0.30
    ang = math.radians(45.0)
    head_ang = math.radians(30.0)
    a1 = ang + math.pi - head_ang
    a2 = ang + math.pi + head_ang
    p.moveTo(tip_x, tip_y)
    p.lineTo(
        tip_x + barb * math.cos(a1),
        tip_y - barb * math.sin(a1),
    )
    p.moveTo(tip_x, tip_y)
    p.lineTo(
        tip_x + barb * math.cos(a2),
        tip_y - barb * math.sin(a2),
    )


def _circular_runout() -> QPainterPath:
    p = QPainterPath()
    _arrow_tilted(p, x=0.15, y_tail=0.85, length=0.65)
    return p


def _total_runout() -> QPainterPath:
    p = QPainterPath()
    _arrow_tilted(p, x=0.10, y_tail=0.85, length=0.65)
    _arrow_tilted(p, x=0.25, y_tail=0.85, length=0.65)
    return p


_SYMBOL_BUILDERS: dict[Characteristic, callable] = {
    Characteristic.STRAIGHTNESS: _straightness,
    Characteristic.FLATNESS: _flatness,
    Characteristic.CIRCULARITY: _circularity,
    Characteristic.CYLINDRICITY: _cylindricity,
    Characteristic.PROFILE_LINE: _profile_line,
    Characteristic.PROFILE_SURFACE: _profile_surface,
    Characteristic.PARALLELISM: _parallelism,
    Characteristic.PERPENDICULARITY: _perpendicularity,
    Characteristic.ANGULARITY: _angularity,
    Characteristic.POSITION: _position,
    Characteristic.CONCENTRICITY: _concentricity,
    Characteristic.SYMMETRY: _symmetry,
    Characteristic.CIRCULAR_RUNOUT: _circular_runout,
    Characteristic.TOTAL_RUNOUT: _total_runout,
}


def symbol_path(characteristic: Characteristic) -> QPainterPath:
    """Return a fresh unit-box QPainterPath for the given characteristic."""
    return _SYMBOL_BUILDERS[characteristic]()
