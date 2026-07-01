"""SelectionToolbar: floating quick-action bar shown near the current
selection, Canva/Figma-style. Puts the most common actions (color,
stroke, duplicate, delete) within a short reach of the selection
instead of requiring a trip to the Properties dock or the menu bar.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton, QWidget


_ACCENT = "#1E88E5"
_FIELD_BORDER = "#9aa0a6"

_TOOLBAR_QSS = f"""
#SelectionToolbar {{
    background-color: palette(window);
    border: 1px solid {_ACCENT};
    border-radius: 6px;
}}
#SelectionToolbar QToolButton {{
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 6px;
}}
#SelectionToolbar QToolButton:hover {{
    border: 1px solid {_FIELD_BORDER};
}}
"""


def _swatch_icon(color: QColor, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(0, 0, 0, 120))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return QIcon(pm)


class SelectionToolbar(QFrame):
    """Floating pill of quick actions. Parent it to the view's viewport."""

    colorClicked = Signal()
    strokeClicked = Signal()
    duplicateClicked = Signal()
    deleteClicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("SelectionToolbar")
        self.setFrameShape(QFrame.StyledPanel)
        self.setAutoFillBackground(True)
        self.setStyleSheet(_TOOLBAR_QSS)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(2)

        self._color_btn = QToolButton(self)
        self._color_btn.setIconSize(QSize(16, 16))
        self._color_btn.setToolTip("Change color")
        self._color_btn.clicked.connect(self.colorClicked)
        lay.addWidget(self._color_btn)

        self._stroke_btn = QToolButton(self)
        self._stroke_btn.setToolTip("Change stroke width")
        self._stroke_btn.clicked.connect(self.strokeClicked)
        lay.addWidget(self._stroke_btn)

        lay.addWidget(self._v_separator())

        dup_btn = QToolButton(self)
        dup_btn.setText("Duplicate")
        dup_btn.setToolTip("Duplicate (Ctrl+D)")
        dup_btn.clicked.connect(self.duplicateClicked)
        lay.addWidget(dup_btn)

        del_btn = QToolButton(self)
        del_btn.setText("Delete")
        del_btn.setToolTip("Delete (Del)")
        del_btn.clicked.connect(self.deleteClicked)
        lay.addWidget(del_btn)

        self.hide()

    def _v_separator(self) -> QFrame:
        line = QFrame(self)
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def set_swatch_color(self, color: QColor) -> None:
        self._color_btn.setIcon(_swatch_icon(color))

    def set_stroke_label(self, width: float) -> None:
        self._stroke_btn.setText(f"{width:g} px")
