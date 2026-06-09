"""AnnotationList dock: shows the current page's annotations.

Selection is two-way synced with the scene. The list is rebuilt on
demand via `refresh()` -- typically after a page switch, an undo/redo,
or a mutation through the tool dispatch layer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDockWidget,
    QGraphicsItem,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from annoter.views.items.base import AnnotationItem


_KIND_LABELS: dict[str, str] = {
    "rect": "Rectangle",
    "ellipse": "Ellipse",
    "line": "Line",
    "arrow": "Arrow",
    "ink": "Freehand",
    "text": "Text",
    "gdt": "GD&T",
}


def _color_dot(color: QColor, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(0, 0, 0, 80))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


class AnnotationListDock(QDockWidget):
    """Right-side list of annotations on the current page."""

    deleteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Annotations", parent)
        self.setObjectName("AnnotationListDock")

        self._list = QListWidget(self)
        self._list.setSelectionMode(QListWidget.ExtendedSelection)
        self._list.itemSelectionChanged.connect(self._on_list_selection)
        self.setWidget(self._list)

        self._page_item: QGraphicsItem | None = None
        self._syncing: bool = False

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def set_page_item(self, page_item: QGraphicsItem | None) -> None:
        self._page_item = page_item
        self.refresh()

    def refresh(self) -> None:
        self._syncing = True
        try:
            self._list.clear()
            if self._page_item is None:
                return
            for child in self._page_item.childItems():
                if not isinstance(child, AnnotationItem):
                    continue
                label = _KIND_LABELS.get(
                    child.KIND, child.KIND or "Annotation"
                )
                row = QListWidgetItem(_color_dot(child.color()), label)
                row.setData(Qt.UserRole, child)
                row.setSelected(child.isSelected())
                self._list.addItem(row)
        finally:
            self._syncing = False

    def sync_selection_from_scene(self) -> None:
        """Reflect the current scene selection in the list."""
        self._syncing = True
        try:
            for i in range(self._list.count()):
                row = self._list.item(i)
                item = row.data(Qt.UserRole)
                row.setSelected(
                    isinstance(item, AnnotationItem) and item.isSelected()
                )
        finally:
            self._syncing = False

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    def _on_list_selection(self) -> None:
        if self._syncing:
            return
        selected = {
            row.data(Qt.UserRole) for row in self._list.selectedItems()
        }
        for i in range(self._list.count()):
            item = self._list.item(i).data(Qt.UserRole)
            if isinstance(item, AnnotationItem):
                item.setSelected(item in selected)
