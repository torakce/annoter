"""AnnotationPicker: Alt+Click list of the annotations under the cursor.

On a drawing, annotations pile up -- a cloud around a GD&T frame around
a text -- and a plain click always takes the topmost. Alt+Click (Select
tool) opens this list of everything stacked at the click, topmost
first, named like the Annotations panel names them (the selected ones
in bold). Hovering an entry outlines that annotation on the page;
clicking it selects it (and only it). The widget is dumb: MainWindow
hands it the items and two callbacks (highlight, pick).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QMenu, QWidget

from annoter.views.annotation_list import describe
from annoter.views.items.base import AnnotationItem
from annoter.views.line_icons import line_icon

HEADER = "Annotations here (top first)"


class AnnotationPicker(QMenu):
    def __init__(
        self,
        items: list[AnnotationItem],
        icon_color: QColor,
        on_hover: Callable[[AnnotationItem | None], None],
        on_pick: Callable[[AnnotationItem], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AnnotationPicker")
        self._items = list(items)
        self._actions: list[QAction] = []
        header = self.addAction(HEADER)
        header.setEnabled(False)
        self.addSeparator()
        for item in self._items:
            title, sub, glyph = describe(item)
            text = f"{title}  ·  {sub}" if sub else title
            act = self.addAction(line_icon(glyph, icon_color), text)
            if item.isSelected():
                # Bold rather than a check mark: the icon takes the
                # check's column in the themed menu.
                font = act.font()
                font.setBold(True)
                act.setFont(font)
            act.hovered.connect(lambda it=item: on_hover(it))
            act.triggered.connect(lambda _c=False, it=item: on_pick(it))
            self._actions.append(act)
        # Leaving the list (pick, Esc, click elsewhere) drops the outline.
        self.aboutToHide.connect(lambda: on_hover(None))

    def items(self) -> list[AnnotationItem]:
        return list(self._items)

    def labels(self) -> list[str]:
        return [a.text() for a in self._actions]

    def entry(self, index: int) -> QAction:
        return self._actions[index]
