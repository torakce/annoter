"""PageThumbnailDock: vertical strip of page thumbnails for navigation.

Makes multi-page PDFs obvious at a glance (Discussion #1, item 10) and
turns page switching into a single click. Thumbnails are rendered
lazily, one per event-loop tick, so opening a 200-page document never
blocks the UI; each page shows a placeholder until its render lands.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QWidget,
)

from annoter.services.pdf_render import PageRenderer

THUMB_MAX_PX = 140


def _placeholder(size: int = THUMB_MAX_PX) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(QColor(0, 0, 0, 60))
    p.setBrush(QColor(255, 255, 255))
    m = size // 8
    p.drawRect(m, m, size - 2 * m, size - 2 * m)
    p.end()
    return QIcon(pm)


class PageThumbnailDock(QDockWidget):
    """Dockable list of page thumbnails; clicking one navigates."""

    pageClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Pages", parent)
        self.setObjectName("PageThumbnailDock")
        self._renderer: PageRenderer | None = None
        self._pending: list[int] = []

        self._list = QListWidget(self)
        self._list.setViewMode(QListWidget.IconMode)
        self._list.setIconSize(QSize(THUMB_MAX_PX, THUMB_MAX_PX))
        self._list.setMovement(QListWidget.Static)
        self._list.setResizeMode(QListWidget.Adjust)
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(8)
        self._list.itemClicked.connect(
            lambda it: self.pageClicked.emit(self._list.row(it))
        )
        self.setWidget(self._list)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)

    # ------------------------------------------------------------------
    # document lifecycle
    # ------------------------------------------------------------------
    def set_document(
        self, renderer: PageRenderer | None, page_count: int = 0
    ) -> None:
        """Rebuild the list for a new document (None clears it)."""
        self._timer.stop()
        self._pending = []
        self._renderer = renderer
        self._list.clear()
        if renderer is None or page_count <= 0:
            return
        ph = _placeholder()
        for i in range(page_count):
            item = QListWidgetItem(ph, f"Page {i + 1}")
            item.setTextAlignment(Qt.AlignHCenter)
            self._list.addItem(item)
        self._pending = list(range(page_count))
        self._timer.start()

    def set_current_page(self, index: int) -> None:
        if 0 <= index < self._list.count():
            self._list.setCurrentRow(index)

    def refresh_page(self, index: int) -> None:
        """Re-render one page's thumbnail (e.g. after a save)."""
        if self._renderer is None:
            return
        if index not in self._pending and 0 <= index < self._list.count():
            self._pending.append(index)
            self._timer.start()

    # ------------------------------------------------------------------
    # lazy rendering: one page per event-loop tick
    # ------------------------------------------------------------------
    def _render_next(self) -> None:
        if self._renderer is None or not self._pending:
            return
        index = self._pending.pop(0)
        try:
            pm = self._renderer.render_thumbnail(index, THUMB_MAX_PX)
        except Exception:
            pm = None  # page unreadable: keep the placeholder
        if pm is not None and index < self._list.count():
            self._list.item(index).setIcon(QIcon(pm))
        if self._pending:
            self._timer.start()
