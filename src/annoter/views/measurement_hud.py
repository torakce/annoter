"""MeasurementHud: live size/length/angle readout while drawing or
resizing a shape, mirroring PowerPoint/Figma's in-canvas dimension
tooltip.

Values are in PDF points -- the annotation's own on-page size (same
unit the persistence layer writes), not a calibrated real-world
measurement (scale calibration is explicitly out of scope for v1).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QLabel, QWidget


class MeasurementHud(QLabel):
    """Small floating label parented to the view's viewport."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("MeasurementHud")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            "QLabel#MeasurementHud {"
            " background-color: rgba(30, 30, 30, 225);"
            " color: white;"
            " border-radius: 4px;"
            " padding: 3px 6px;"
            " font-size: 11px;"
            "}"
        )
        self.hide()

    def show_rect(self, w_pt: float, h_pt: float, anchor: QPoint) -> None:
        self.setText(f"{abs(w_pt):.1f} x {abs(h_pt):.1f} pt")
        self._place(anchor)

    def show_line(
        self, length_pt: float, angle_deg: float, anchor: QPoint
    ) -> None:
        # Screen space has y-down, so a raw atan2 angle reads backwards
        # to a human; flip it to the usual 0=east/CCW-positive drawing
        # convention before display.
        display_angle = (-angle_deg) % 360
        self.setText(f"{length_pt:.1f} pt @ {display_angle:.0f}°")
        self._place(anchor)

    def _place(self, anchor: QPoint) -> None:
        self.adjustSize()
        self.move(anchor.x() + 16, anchor.y() + 16)
        self.show()
        self.raise_()
