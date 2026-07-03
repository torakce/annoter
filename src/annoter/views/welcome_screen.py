"""WelcomeScreen: home page shown when no document is open.

Discussion #1, item 12: instead of an empty gray viewport, the app opens
on a start page offering Open / New blank document and a clickable grid
of recent files with real first-page thumbnails. Thumbnails are rendered
lazily (one file per event-loop tick) since each one means opening the
PDF; a file that fails to open keeps a generic placeholder and can be
removed from the list via its context menu.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

THUMB_PX = 160


def _generic_icon(size: int = THUMB_PX, broken: bool = False) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(0, 0, 0, 70))
    p.setBrush(QColor(255, 255, 255))
    m = size // 6
    p.drawRect(m, m // 2, size - 2 * m, size - m)
    if broken:
        p.setPen(QColor("#E53935"))
        p.drawLine(m, m // 2, size - m, size - m // 2)
        p.drawLine(size - m, m // 2, m, size - m // 2)
    p.end()
    return QIcon(pm)


class WelcomeScreen(QWidget):
    """Start page: open / blank-document actions + recent files grid."""

    openRequested = Signal()
    blankRequested = Signal()
    openPathRequested = Signal(str)
    removePathRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pending: list[int] = []  # rows awaiting a thumbnail

        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(16)

        title = QLabel("Annoter")
        f = title.font()
        f.setPointSize(f.pointSize() + 10)
        f.setBold(True)
        title.setFont(f)
        outer.addWidget(title)

        subtitle = QLabel("PDF annotation for engineering drawings")
        outer.addWidget(subtitle)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._btn_open = QPushButton("Open PDF...")
        self._btn_open.clicked.connect(self.openRequested)
        buttons.addWidget(self._btn_open)
        self._btn_blank = QPushButton("New blank document")
        self._btn_blank.clicked.connect(self.blankRequested)
        buttons.addWidget(self._btn_blank)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        self._recent_label = QLabel("Recent documents")
        outer.addWidget(self._recent_label)

        self._list = QListWidget(self)
        self._list.setViewMode(QListWidget.IconMode)
        self._list.setIconSize(QSize(THUMB_PX, THUMB_PX))
        self._list.setMovement(QListWidget.Static)
        self._list.setResizeMode(QListWidget.Adjust)
        self._list.setSpacing(12)
        self._list.setWordWrap(True)
        self._list.itemActivated.connect(self._on_item_open)
        self._list.itemClicked.connect(self._on_item_open)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        outer.addWidget(self._list, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)

    # ------------------------------------------------------------------
    # recent files
    # ------------------------------------------------------------------
    def set_recent(self, paths: list[str]) -> None:
        self._timer.stop()
        self._pending = []
        self._list.clear()
        ph = _generic_icon()
        for path in paths:
            p = Path(path)
            item = QListWidgetItem(ph, p.name)
            item.setData(Qt.UserRole, str(path))
            item.setToolTip(str(path))
            item.setTextAlignment(Qt.AlignHCenter)
            self._list.addItem(item)
        has_recent = self._list.count() > 0
        self._recent_label.setVisible(has_recent)
        self._list.setVisible(has_recent)
        if has_recent:
            self._pending = list(range(self._list.count()))
            self._timer.start()

    def _on_item_open(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.openPathRequested.emit(str(path))

    def _on_context_menu(self, pos) -> None:  # noqa: ANN001
        item = self._list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_open = menu.addAction("Open")
        act_remove = menu.addAction("Remove from list")
        chosen = menu.exec(self._list.mapToGlobal(pos))
        if chosen is act_open:
            self._on_item_open(item)
        elif chosen is act_remove:
            self.removePathRequested.emit(str(item.data(Qt.UserRole)))

    # ------------------------------------------------------------------
    # lazy thumbnails: one file per event-loop tick
    # ------------------------------------------------------------------
    def _render_next(self) -> None:
        if not self._pending:
            return
        row = self._pending.pop(0)
        item = self._list.item(row)
        if item is not None:
            icon = self._thumbnail_for(str(item.data(Qt.UserRole)))
            item.setIcon(icon)
        if self._pending:
            self._timer.start()

    @staticmethod
    def _thumbnail_for(path: str) -> QIcon:
        """First-page thumbnail of `path`, or a broken-file placeholder.

        Opens the PDF just long enough to rasterize page 1; oversized or
        corrupt files simply fall back to the placeholder rather than
        raising into the event loop.
        """
        import fitz
        from PySide6.QtGui import QImage

        try:
            doc = fitz.open(path)
            try:
                if doc.needs_pass or doc.page_count < 1:
                    return _generic_icon(broken=True)
                page = doc[0]
                side = max(page.rect.width, page.rect.height, 1.0)
                zoom = THUMB_PX / side
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(zoom, zoom), alpha=False
                )
                image = QImage(
                    pix.samples,
                    pix.width,
                    pix.height,
                    pix.stride,
                    QImage.Format_RGB888,
                ).copy()
                return QIcon(QPixmap.fromImage(image))
            finally:
                doc.close()
        except Exception:
            return _generic_icon(broken=True)
