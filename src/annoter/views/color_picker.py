"""Office-style quick color picker menu.

A compact popup with a grid of standard colors and a "More colors..."
entry that opens the full QColorDialog only when actually needed --
mirroring Microsoft Office's color dropdowns instead of jumping
straight to the full color wheel (Discussion #1 follow-up feedback).

Shared by the toolbar color control, the selection color actions and
the Properties dock color buttons.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QGridLayout,
    QMenu,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from annoter.views.icons import color_swatch_icon

# Two Office-like rows: neutrals + warm, then cool + accents.
STANDARD_COLORS: list[str] = [
    "#000000", "#616161", "#9E9E9E", "#FFFFFF",
    "#B71C1C", "#E53935", "#FB8C00", "#FDD835",
    "#43A047", "#1B5E20", "#29B6F6", "#1E88E5",
    "#0D47A1", "#8E24AA", "#6D4C41", "#EC407A",
]

_COLUMNS = 8
_SWATCH_PX = 18


class ColorPickerMenu(QMenu):
    """Popup: swatch grid + "More colors..." fallback dialog."""

    colorSelected = Signal(QColor)

    def __init__(
        self,
        initial: QColor | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial = QColor(initial) if initial is not None else QColor()

        grid_host = QWidget(self)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(8, 6, 8, 6)
        grid.setSpacing(2)
        for i, hex_ in enumerate(STANDARD_COLORS):
            color = QColor(hex_)
            btn = QToolButton(grid_host)
            btn.setAutoRaise(True)
            btn.setIcon(color_swatch_icon(color, size=_SWATCH_PX))
            btn.setIconSize(QSize(_SWATCH_PX, _SWATCH_PX))
            btn.setToolTip(color.name())
            btn.clicked.connect(
                lambda _checked=False, c=color: self._choose(c)
            )
            grid.addWidget(btn, i // _COLUMNS, i % _COLUMNS)

        grid_action = QWidgetAction(self)
        grid_action.setDefaultWidget(grid_host)
        self.addAction(grid_action)
        self.addSeparator()
        more = self.addAction("More colors...")
        more.triggered.connect(self._more_colors)

    def _choose(self, color: QColor) -> None:
        self.close()
        self.colorSelected.emit(QColor(color))

    def _more_colors(self) -> None:
        initial = self._initial if self._initial.isValid() else QColor("#E53935")
        c = QColorDialog.getColor(
            initial, self.parentWidget(), "Pick a color"
        )
        if c.isValid():
            self.colorSelected.emit(QColor(c))


def popup_color_picker(
    parent: QWidget | None,
    initial: QColor | None,
    on_picked: Callable[[QColor], None],
    global_pos: QPoint | None = None,
) -> ColorPickerMenu:
    """Open a ColorPickerMenu at `global_pos` (or the cursor) and route
    the chosen color to `on_picked`. Returns the menu (kept alive by
    Qt's popup ownership until it closes)."""
    from PySide6.QtGui import QCursor

    menu = ColorPickerMenu(initial, parent)
    menu.colorSelected.connect(on_picked)
    menu.setAttribute(Qt.WA_DeleteOnClose)
    menu.popup(global_pos if global_pos is not None else QCursor.pos())
    return menu
