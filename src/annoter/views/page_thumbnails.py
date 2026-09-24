"""PageThumbnailList: vertical strip of page thumbnails for navigation.

Makes multi-page PDFs obvious at a glance (Discussion #1, item 10) and
turns page switching into a single click. Thumbnails are rendered
lazily, one per event-loop tick, so opening a 200-page document never
blocks the UI; each page shows a placeholder until its render lands.

UI redesign, Lot E: the list is no longer a dock of its own but the
"Pages" tab of the left `DocumentSidebar`. A delegate draws each page
as a framed thumbnail (visible on a white panel), the current page with
an accent ring, and under it the page number and how many annotations
the page carries.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from annoter.services.pdf_render import PageRenderer

THUMB_MAX_PX = 180
_ROLE_COUNT = Qt.UserRole + 1
_CAPTION_H = 24
_PAD = 8


def _placeholder(size: int = THUMB_MAX_PX) -> QIcon:
    pm = QPixmap(size, int(size * 0.72))
    pm.fill(QColor(255, 255, 255))
    return QIcon(pm)


def count_caption(n: int) -> str:
    if n <= 0:
        return ""
    return f"{n} annotation" if n == 1 else f"{n} annotations"


def _thumb_pixmap(icon: object) -> tuple[QPixmap, int, int]:
    """The thumbnail pixmap and its logical width / height."""
    if isinstance(icon, QIcon) and not icon.isNull():
        pm = icon.pixmap(QSize(THUMB_MAX_PX, THUMB_MAX_PX))
        if not pm.isNull():
            dpr = pm.devicePixelRatio() or 1.0
            return pm, int(pm.width() / dpr), int(pm.height() / dpr)
    return QPixmap(), THUMB_MAX_PX, int(THUMB_MAX_PX * 0.72)


class _ThumbDelegate(QStyledItemDelegate):
    """Framed thumbnail + "page number / annotation count" caption."""

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        _pm, _w, h = _thumb_pixmap(index.data(Qt.DecorationRole))
        return QSize(THUMB_MAX_PX + 2 * _PAD, h + _CAPTION_H + 2 * _PAD)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        pal = option.palette
        r = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = bool(option.state & QStyle.State_MouseOver)
        current = bool(option.state & QStyle.State_Selected)

        pm, pw, ph = _thumb_pixmap(index.data(Qt.DecorationRole))
        x = r.left() + (r.width() - pw) // 2
        y = r.top() + _PAD
        thumb = QRect(x, y, pw, ph)

        if current:
            ring = QPen(pal.highlight().color(), 2.0)
            painter.setPen(ring)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(thumb.adjusted(-3, -3, 3, 3), 4, 4)
        if not pm.isNull():
            painter.drawPixmap(thumb, pm)
        else:
            painter.fillRect(thumb, QColor(255, 255, 255))
        border = pal.mid().color() if not hovered else pal.dark().color()
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(thumb.adjusted(0, 0, -1, -1))

        cap = QRect(thumb.left(), thumb.bottom() + 6, pw, _CAPTION_H - 6)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(
            pal.highlight().color() if current else pal.text().color()
        )
        painter.drawText(
            cap, Qt.AlignLeft | Qt.AlignVCenter, str(index.row() + 1)
        )
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(pal.placeholderText().color())
        n = index.data(_ROLE_COUNT) or 0
        painter.drawText(
            cap, Qt.AlignRight | Qt.AlignVCenter, count_caption(int(n))
        )
        painter.restore()


class PageThumbnailList(QWidget):
    """List of page thumbnails.

    Clicking a thumbnail navigates; dragging one to another position
    reorders the document's pages (`pageMoved(from, to)` -- MainWindow
    applies the move to the PDF and remaps its per-page state).
    """

    pageClicked = Signal(int)
    pageMoved = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageThumbnailList")
        self._renderer: PageRenderer | None = None
        self._pending: list[int] = []

        self._list = QListWidget(self)
        self._list.setObjectName("PageThumbnails")
        self._list.setViewMode(QListWidget.ListMode)
        self._list.setItemDelegate(_ThumbDelegate(self._list))
        self._list.setMouseTracking(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._list.setSpacing(2)
        self._list.setDragDropMode(QListWidget.InternalMove)
        self._list.setDefaultDropAction(Qt.MoveAction)
        self._list.itemClicked.connect(
            lambda it: self.pageClicked.emit(self._list.row(it))
        )
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(self._list)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)

    def _on_rows_moved(
        self, _parent, start: int, _end: int, _dest, row: int
    ) -> None:  # noqa: ANN001
        # Qt reports the destination in pre-removal indexing; convert to
        # the page's FINAL index. Deferred so the drop finishes before
        # MainWindow rebuilds this very list.
        final = row if row < start else row - 1
        if final != start:
            QTimer.singleShot(
                0, lambda f=start, t=final: self.pageMoved.emit(f, t)
            )

    # ------------------------------------------------------------------
    # document lifecycle
    # ------------------------------------------------------------------
    def count(self) -> int:
        return self._list.count()

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
            item = QListWidgetItem(ph, "")
            item.setToolTip(f"Page {i + 1}")
            item.setData(_ROLE_COUNT, 0)
            self._list.addItem(item)
        self._pending = list(range(page_count))
        self._timer.start()

    def set_current_page(self, index: int) -> None:
        if 0 <= index < self._list.count():
            self._list.setCurrentRow(index)

    def set_annotation_counts(self, counts: dict[int, int]) -> None:
        """Per-page annotation counts shown under each thumbnail."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            n = int(counts.get(i, 0))
            item.setData(_ROLE_COUNT, n)
            caption = count_caption(n)
            item.setToolTip(
                f"Page {i + 1}" + (f" · {caption}" if caption else "")
            )

    def annotation_count(self, index: int) -> int:
        item = self._list.item(index)
        return int(item.data(_ROLE_COUNT) or 0) if item else 0

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
