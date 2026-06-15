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
    # Parallelogram leaning right (top edge shifted toward the right).
    p.moveTo(0.05, 0.70)
    p.lineTo(0.75, 0.70)
    p.lineTo(0.95, 0.30)
    p.lineTo(0.25, 0.30)
    p.closeSubpath()
    return p


def _circularity() -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QRectF(0.15, 0.15, 0.70, 0.70))
    return p


def _cylindricity() -> QPainterPath:
    p = QPainterPath()
    # Circle with two oblique lines tangent to its sides: /O/.
    # Endpoints solved so each line sits exactly one radius (0.25)
    # away from the center (0.5, 0.5) at a ~15-degree lean.
    p.addEllipse(QRectF(0.25, 0.25, 0.50, 0.50))
    p.moveTo(0.12, 0.95)
    p.lineTo(0.36, 0.05)
    p.moveTo(0.64, 0.95)
    p.lineTo(0.88, 0.05)
    return p


def _profile_line() -> QPainterPath:
    p = QPainterPath()
    # Open dome: the top half of a wide ellipse.
    rect = QRectF(0.10, 0.25, 0.80, 0.70)
    p.arcMoveTo(rect, 0.0)
    p.arcTo(rect, 0.0, 180.0)
    return p


def _profile_surface() -> QPainterPath:
    p = QPainterPath()
    # Same dome closed by its chord (half-disc).
    rect = QRectF(0.10, 0.25, 0.80, 0.70)
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
    # Wedge opening right at roughly 30 degrees.
    p.moveTo(0.10, 0.80)
    p.lineTo(0.90, 0.80)
    p.moveTo(0.10, 0.80)
    p.lineTo(0.90, 0.30)
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
    p.addEllipse(QRectF(0.12, 0.12, 0.76, 0.76))
    p.addEllipse(QRectF(0.28, 0.28, 0.44, 0.44))
    return p


def _symmetry() -> QPainterPath:
    p = QPainterPath()
    # Three stacked bars, the middle one longer.
    p.moveTo(0.22, 0.30)
    p.lineTo(0.78, 0.30)
    p.moveTo(0.08, 0.50)
    p.lineTo(0.92, 0.50)
    p.moveTo(0.22, 0.70)
    p.lineTo(0.78, 0.70)
    return p


def _arrow_tilted(p: QPainterPath, x: float, y_tail: float, length: float) -> None:
    """Draw a 45-degree arrow whose tip is at (x + length, y_tail - length).

    The head is a closed (outlined) triangle, like the printed symbol;
    the shaft stops at the head's base so it does not poke through.
    """
    tip_x = x + length
    tip_y = y_tail - length
    barb = 0.20
    head_ang = math.radians(25.0)
    # Shaft points up-right at -45 degrees (y axis points down); the
    # two barbs fan out around the opposite direction.
    shaft = math.radians(-45.0)
    a1 = shaft + math.pi - head_ang
    a2 = shaft + math.pi + head_ang
    p1 = QPointF(
        tip_x + barb * math.cos(a1), tip_y + barb * math.sin(a1)
    )
    p2 = QPointF(
        tip_x + barb * math.cos(a2), tip_y + barb * math.sin(a2)
    )
    base_mid = QPointF(
        (p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0
    )
    p.moveTo(x, y_tail)
    p.lineTo(base_mid)
    p.moveTo(p1)
    p.lineTo(tip_x, tip_y)
    p.lineTo(p2)
    p.closeSubpath()


def _circular_runout() -> QPainterPath:
    p = QPainterPath()
    _arrow_tilted(p, x=0.18, y_tail=0.82, length=0.62)
    return p


def _total_runout() -> QPainterPath:
    p = QPainterPath()
    # Two parallel arrows joined at their tails by a base line.
    _arrow_tilted(p, x=0.08, y_tail=0.88, length=0.55)
    _arrow_tilted(p, x=0.38, y_tail=0.88, length=0.55)
    p.moveTo(0.08, 0.88)
    p.lineTo(0.38, 0.88)
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
