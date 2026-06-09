"""GdtPalette: dockable widget grouping the 14 ISO 1101 symbols.

Clicking a symbol button selects the GD&T tool and pre-arms the next
GD&T placement with the chosen characteristic.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDockWidget,
    QFrame,
    QGridLayout,
    QGroupBox,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from annoter.model.gdt import (
    CHARACTERISTIC_META,
    Characteristic,
    Family,
    by_family,
)
from annoter.views.items.gdt_symbols import symbol_path


_ICON_SIZE = 28


def _symbol_icon(
    c: Characteristic,
    size: int = _ICON_SIZE,
    color: QColor | None = None,
) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color if color is not None else QColor("#212121"))
    pen.setCosmetic(True)
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    inset = size * 0.12
    painter.translate(inset, inset)
    painter.scale(size - 2 * inset, size - 2 * inset)
    painter.drawPath(symbol_path(c))
    painter.end()
    return QIcon(pm)


class GdtPalette(QDockWidget):
    """Right-side dock with the 14 ISO 1101 characteristics."""

    characteristicChosen = Signal(object)  # Characteristic

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("GD&T", parent)
        self.setObjectName("GdtPaletteDock")

        self._icon_color: QColor = QColor("#212121")
        self._buttons: list[tuple[QToolButton, Characteristic]] = []

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        for fam in Family:
            layout.addWidget(self._build_family_group(fam))
        layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.setWidget(scroll)

    def _build_family_group(self, fam: Family) -> QGroupBox:
        box = QGroupBox(fam.value)
        grid = QGridLayout(box)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for idx, c in enumerate(by_family()[fam]):
            _, name = CHARACTERISTIC_META[c]
            btn = QToolButton()
            btn.setIcon(_symbol_icon(c, color=self._icon_color))
            btn.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
            btn.setText(name)
            btn.setToolTip(name)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setMinimumWidth(96)
            btn.clicked.connect(
                lambda _checked=False, ch=c: self.characteristicChosen.emit(ch)
            )
            grid.addWidget(btn, idx // 2, idx % 2)
            self._buttons.append((btn, c))
        return box

    def set_icon_color(self, color: QColor) -> None:
        """Repaint every symbol icon. Called by MainWindow on theme change."""
        self._icon_color = QColor(color)
        for btn, c in self._buttons:
            btn.setIcon(_symbol_icon(c, color=self._icon_color))
